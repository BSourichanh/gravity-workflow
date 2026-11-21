# Sécurité, Hooks Fail-Closed & Budget Règles

## Contrat Antigravity des hooks ([`gravity-plugin/hooks.json`](../gravity-plugin/hooks.json))

Antigravity exécute les commandes de `hooks.json` depuis **la racine du plugin** (`cwd = gravity-plugin/`), tout en transmettant le projet cible sur `stdin` via `cwd` et `workspacePaths[0]`.

- **Structure obligatoire :** `PreToolUse` et `PostToolUse` sont des **tableaux groupés** (`matcher` + `hooks[]`) ; `SessionStart`, `PreInvocation`, `PostInvocation` et `Stop` sont des **tableaux plats**.
- **Règle absolue `exit 0` :** tout code retour non nul ou exception non capturée bascule silencieusement en **fail-open**. Tous les scripts capturent les exceptions (y compris d'import), configurent `stdin`/`stdout`/`stderr` en UTF-8 (`configure_utf8_stdio()`), émettent du JSON valide (`ensure_ascii=True` dans `emit_json()` pour prévenir toute erreur `protojson` sous Windows `cp1252`) et sortent en `0`.
- **Utilitaire partagé [`hook_runtime.py`](../gravity-plugin/hooks/hook_runtime.py) :** lecture `stdin` threadée avec timeout (`1.0 s`), `configure_utf8_stdio()`, `emit_json()` (ASCII-safe), `emit_ok()` (`{}`), `is_gravity_workspace()`, résolution de workspace, chargement dynamique du verrou portable et journalisation non bloquante des événements `deny`/`force_ask` (uniquement dans les workspaces initialisés Gravity ou sur surcharge `GRAVITY_STATE_PATH`) avec `conversation_id` dans `live_state.json`.

---

## Garde-fou `PreToolUse` ([`pre_tool_guard.py`](../gravity-plugin/hooks/pre_tool_guard.py) + [`safety_policy.py`](../gravity-plugin/hooks/safety_policy.py))

Le matcher couvre les outils shell (`run_command`, `execute_command`, `bash`, `shell`), `run_workflow`, d'écriture (`write_to_file`, `write_file`, `replace_file_content`, `edit_file`, `multi_replace_file_content`), de lecture/recherche (`view_file`, `read_file`, `grep_search`, `search_files`, `list_dir`, `find_by_name`) et `call_mcp_tool`. Il reste **toujours actif**, même en modes `fast` et `unsafe`.

Pour l'analyse des commandes shell sans faux positifs inter-commandes ni contournement par guillemets :
- **`split_shell_segments()` :** découpe la ligne de commande sur `;`, `&&`, `||`, `|`, `&` *(hors redirections `2>&1`/`&>` et appel PowerShell `&` en tête)* et `\n` uniquement hors guillemets, préserve les backslashes Windows (`C:\...`) et extrait récursivement (profondeur bornée à `4`) les sous-commandes `$(...)` et `` `...` `` hors single-quotes.
- **`extract_command_head_and_args()` :** tokenise chaque segment en préservant les chemins Windows, déballe les affectations `VAR=val` et wrappers (`sudo`, `env`, `command`, `nohup`, `time`, `&`), puis normalise la tête d'exécutable (`.exe` / chemin).
- **Séparation mono-segment vs pipeline :** `rm` destructeur, `git push` forcé, auto-protection harnais (`_shell_targets_harness`) et exécution inline (`_check_segment_inline_exec`) sont évalués **segment par segment** ; les règles inter-segments / pipelines (`curl|wget ... | sh`, `irm|iwr | iex`, dumps d'environnement, accès `.env`/secrets, `xargs`, `| base64`, `iex`, globbing caché) restent évaluées sur la commande complète (et par segment pour `ENV_DUMP_PATTERN`).

| Niveau de décision | Déclencheurs principaux |
|---|---|
| **`deny`** *(blocage ferme)* | • `rm -rf`/`rm --recursive --force` et PowerShell/CMD `Remove-Item`/`ri`/`del`/`rd -Recurse -Force` (ou `/s /q`, `/s /f`, y compris binaires `.exe`) sur cibles destructrices (`/`, `~`, `$HOME`, `*`, `.`, `..`, `C:\`, ou segment `..`), sans bloquer `/tmp/build_cache`<br>• `git push --force`/`-f`/`+ref` évalué par segment (`_is_git_force_push`, y compris avec flags globaux comme `git -C repo push --force`, `git.exe`, ou en sous-commande `$(...)`, sans fuiter sur un `rm -f` chaîné) ; `DROP DATABASE/SCHEMA/TABLE`, `TRUNCATE TABLE`, `FLUSHALL`/`FLUSHDB`<br>• `printenv`, `env`, `export -p`, `declare -x`, `Get-ChildItem`/`Get-Item`/`Get-Content Env:`, `[Environment]::GetEnvironmentVariables()`, `Get-Variable` ; pipes `curl`/`wget` vers shell et `irm`/`iwr \| iex`<br>• Accès shell, lecture, recherche, parcours, MCP (inspection récursive) ou `run_workflow` ciblant `.env` *(hors `.env.example`/`.sample`/`.template`)*, `secrets/`, `.pem`/`.key`/`.p12`/`.pfx`, clés SSH (`id_rsa`, `id_ed25519`, `.ssh/`)<br>• Auto-protection en écriture, `run_workflow` et shell par segment (`_shell_targets_harness` : tête d'écriture/exécution comme `rm`, `mv`, `Add-Content`, `python`, `sed -i` ou redirection `>`/`>>` hors guillemets conjointe avec un chemin protégé dans le même segment — les commandes en lecture seule comme `git diff -- gravity-plugin/hooks/...` ou `Get-Content` restent autorisées) sur `hooks.json` du harnais (`.agents/`, `gravity-plugin/`, `plugins/gravity/`, racine), les dossiers `hooks/` de Gravity (`.agents/gravity/hooks/`, `gravity-plugin/hooks/`, `plugins/gravity/hooks/`, plugin actif résolu — sans bloquer `gravity-metrics-plugin/hooks/` ni `src/hooks/`) et `01-safety-boundaries.md` |
| **`force_ask`** *(validation humaine, ignore « Always Allow »)* | • Exécution inline ou opaque par segment (`_check_segment_inline_exec`, sensible à la casse sur `-c`/`-e` et arrêté dès `-m`/`--`/argument script positionnel pour ignorer `-C` sur `git -C` chaîné et les flags applicatifs post-script, tout en interceptant `;` entre guillemets, wrappers `sudo`/`env`/`&` et sous-commandes `$(...)`/backticks) : `python`/`py` (`-c`, `-uc`), `node` (`-e`/`-p`/`--eval`/`--print`), `perl` (`-e`/`-E`), `ruby` (`-e`), `sh`/`bash`/`zsh`/`dash` (`-c`/`-lc`, jamais `-C`), `pwsh`/`powershell` (`-c`/`-Command`/`-EncodedCommand`/`-enc` avant `-File`), `cmd` (`/c`, `/k`), `find` (`-exec`/`-execdir`/`-ok`), `eval`, `base64` (`-d`/`--decode`)<br>• Règles globales `OPAQUE_ASK_CMDS` : `xargs`, `\| base64`, `FromBase64String`, `Invoke-Expression`/`iex`, globbing sur fichiers cachés (`.e*`)<br>• Scripts `run_workflow` résolus depuis `Cwd` / `workspace_root` (escalade `force_ask` fail-closed si `ScriptPath` est introuvable) utilisant `subprocess`, `shutil`, `ctypes`, `importlib`, `os.remove`/`os.replace`/`rmdir`, `.rmtree()`/`.unlink()`/`.chmod()`/`.system()`/`.popen()`, `getenv`, `exec`/`eval`/`__import__`, `getattr(..., "environ"\|...)`<br>• `stdin` vide, JSON invalide ou `toolCall` manquant *(fail-closed)* |
| **`allow`** | Opérations régulières sans signal de risque, y compris commandes chaînées (`python -m unittest ...; git -C repo status -sb`, `node build.js; grep -E "TODO" README.md`, `git push origin dev; rm -f /tmp/cache`), flags applicatifs post-script (`python scripts/cli.py -c config.json`, `python -m mypkg.cli -c prod`), inspection Git/lecture seule de fichiers `.py` du harnais (`git -C repo diff -- gravity-plugin/hooks/stop_session_logger.py`, `Get-Content gravity-plugin/hooks/safety_policy.py`), `pytest -q`, `rm -rf /tmp/build_cache`, lecture `src/ui/keyboard.py`, `run_workflow` sur `evals/run_suite.py` ou `review_pipeline.py` |

---

## Hooks `SessionStart`, `PostToolUse` & `Stop`

- **[`session_start_caveman.py`](../gravity-plugin/hooks/session_start_caveman.py) (`SessionStart`, groupe `harness-session-caveman`) :**
  - Charge `.agents/gravity/profile.json` (uniquement si le workspace est initialisé Gravity) et vérifie le niveau `caveman` (`lite` par défaut).
  - Si hors projet Gravity, si `caveman == "off"`, ou si `stdin` est malformé : émet `{}` et sort en `0`.
  - Sinon, injecte une seule fois au démarrage (`injectSteps[0].systemMessage.systemMessage`) la directive compacte produite par `caveman_protocol.build_session_start_prompt()`, préservant le Context Caching Gemini et imposant l'**auto-clarté** (prose complète obligatoire sur alertes/refus `PreToolUse`, confirmations irréversibles et `/grill-me`).
- **[`post_tool_format.py`](../gravity-plugin/hooks/post_tool_format.py) (`PostToolUse`) :**
  - Émet strictement `{}` et sort en `0`.
  - Ignore immédiatement si le workspace a `"verification": "unsafe"` dans `.agents/gravity/profile.json`, si l'outil a retourné `error`, ou si la cible est dans `.agents/`.
  - Exécute `ruff format` / `black --quiet` sur `.py`, ou `prettier --write` uniquement si une config Prettier (`.prettierrc*`, `prettier.config.*`, ou clé `"prettier"` dans `package.json`) existe dans le projet.
- **[`stop_session_logger.py`](../gravity-plugin/hooks/stop_session_logger.py) (`Stop`) :**
  - Ignore immédiatement (émet `{}` en `exit 0` sans créer `.agents/gravity/`) si le workspace n'est pas initialisé Gravity (`is_gravity_workspace()`).
  - Enregistre les champs Antigravity (`conversationId`, `modelName`, `terminationReason`, `executionNum`, `fullyIdle`, `continuationActive`, `transcriptPath`, `error`, `cwd`) sous verrou exclusif dans `.agents/gravity/observability/runs.jsonl`.
  - Déclenche `profile_engine.evolve_on_stop(ws_root, entry)` (y compris l'adaptation `evo` de `caveman` si `caveman_locked: false`) puis émet `{}` en `exit 0`.

---

## Règles projet & budget de contexte

Dans le projet cible (modèles sous [`workspace-template/.agents/rules/`](../workspace-template/.agents/rules/)) :

- **Frontmatter `trigger:` obligatoire** parmi `{"always_on", "model_decision", "glob", "manual"}` (`always_on: true` sans clé `trigger` est ignoré par Antigravity).
  - `trigger: glob` exige un champ `globs` sous forme de chaîne séparée par des virgules (jamais de tableau YAML).
  - `trigger: model_decision` exige une `description` non vide.
- **Limites strictes vérifiées par `validate.py` :**
  - `01-safety-boundaries.md` : `trigger: always_on`, taille `<= 2 048` octets (après expansion `@[label](path)`).
  - `05-git-versioning.md` : `trigger: model_decision` (hors budget `always_on`), conventions Git & versioning réservées au chef d'orchestre uniquement (jamais aux sous-agents ni workers `swarm`), déployée automatiquement par `/gravity` si absente.
  - Chaque fichier de règle : `<= 24 000` octets.
  - Budget actif agrégé (`AGENTS.md` + `GEMINI.md` + règles `always_on`) : `< 20 000` tokens estimés (`defaultRulesBudget`), préservant un préfixe statique pour le Context Caching Gemini.
