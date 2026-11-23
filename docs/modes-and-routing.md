# Modes `/gravity-workflow`, Routage & Profil Consultatif

Le plugin expose le skill principal `/gravity-workflow` ([`SKILL.md`](../plugin/skills/gravity-workflow/SKILL.md)) et son skill compagnon `/caveman` ([`caveman/SKILL.md`](../plugin/skills/caveman/SKILL.md)), pilotés par [`profile_engine.py`](../plugin/observability/profile_engine.py) et [`caveman_protocol.py`](../plugin/observability/caveman_protocol.py), et persistés de façon centralisée sous **`$GRAVITY_WORKFLOW_HOME/workspaces/<slug>-<sha256_8>/profile.json`** (`$GRAVITY_WORKFLOW_HOME` > `$GRAVITY_HOME/workflow` > `~/.gemini/gravity/workflow/`, avec repli lecture seule sur `<ws>/.agents/gravity-workflow/profile.json` si présent — zéro fichier d'état écrit dans le workspace cible).

---

## Grammaire d'invocation & profils

`/gravity-workflow` accepte des mots-clés positionnels en tête de commande ou l'une des 3 sous-commandes utilitaires :

| Profil / Commande | Mots-clés & alias | Défaut | Effet |
|---|---|---|---|
| **Profil d'exécution (`mode`)** | `quick` *(alias `low`)*, `standard` *(alias `medium`)*, `deep` *(alias `high`, `ultra`)*, `evo` *(alias `evolutive`)* | **`evo` (`standard`)** | `quick`/`standard`/`deep` verrouillent (`locked: true`) jusqu'à `/gravity-workflow evo` ; `evo` maintient le profil actif et publie `suggested_profile` |
| **Alias historiques tolérés** | `slow`, `fast` | — | Axe `slow`/`fast` supprimé ; mots-clés acceptés sans erreur en alias ignoré |
| **Vérification éphémère** | `unsafe`, `--no-verify` *(vs `safe`)* | **`safe`** | Strictement éphémère sur **1 tâche ou 1 invocation `run_workflow`**, jamais persisté dans `profile.json`. Les hooks `PreToolUse` restent **toujours actifs** |
| **Utilitaires** | `status`, `evals [CASE-ID...]`, `init [quickstart\|full]` | — | `status` (bilan 4 blocs + `suggested_profile`), `evals` (suite parallèle), `init` (déploie `.agents/` sur demande uniquement) |

### Règles de parsing anti-ambiguïté
- Correspondance **mot entier uniquement** en tête de commande (`quick-fix` ou `deep-link` ne sont pas interprétés comme `quick` ou `deep`).
- `status`, `evals` et `init` ne déclenchent une sous-commande que s'ils sont seuls ou suivis uniquement de leurs arguments dédiés (`CASE-ID...` pour `evals`, `quickstart|full` pour `init`) ; sinon l'entrée est exécutée comme `<tâche>`.
- En CLI hors chat, [`profile_engine.py`](../plugin/observability/profile_engine.py) expose `get`, `set` (`--mode`, `--caveman`, `--plan-model`, `--build-model`, `--review-model`, `--allow-pro`, `--reason`, `--json`, plus alias tolérés `--pace` et `--verify safe`) et `evolve`.

---

## Matrice des 3 profils & attribution des modèles

Les clés retournées par `get_active_preset()` dans `profile_engine.py` sont strictement limitées aux champs réellement lus au runtime (zéro clé morte ; `skip_verification` dérivée uniquement de l'argument éphémère `no_verify`/`verification_override="unsafe"`) :

| Rôle / Paramètre | `quick` *(alias `low`)* | `standard` *(défaut, alias `medium`)* | `deep` *(alias `high`/`ultra`)* |
|---|---|---|---|
| **Orchestration** | Direct mono-fil (zéro `review_pipeline`) | Direct sur tâche triviale ; `review_pipeline.py` sur lot significatif (sans `Planner` si `plan` fourni) | `review_pipeline.py` complet (`2` remédiations) ; suggestion `/plan /grill-me`, `/boost` ou `/swarm` à l'initiative utilisateur |
| **`plan_model`** | `flash` *(sauté par défaut)* | `flash` *(sauté si `args["plan"]` fourni)* | `pro` *(sauté si `args["plan"]` fourni ; repli `flash` si `allow_pro: false`)* |
| **`build_model`** | `flash` | `flash` | `flash` *(`pro` sur override explicite)* |
| **`review_model`** | `flash` *(sauté par défaut)* | `pro` *(repli `flash` si `allow_pro: false`)* | `pro` *(repli `flash` si `allow_pro: false`)* |
| **`max_remediation_rounds`** | `0` *(0–1 si forcé)* | `1` | `2` |
| **`skip_separate_planner`** | `True` | `False` *(`True` si `args["plan"]` fourni)* | `False` *(`True` si `args["plan"]` fourni)* |
| **`eval_candidate` / `eval_judge`** | `flash` / `flash` | `flash` / `pro` *(repli `flash`)* | `flash` / `pro` *(repli `flash`)* |

**Règles de surcharge :**
- **`"allow_pro": false`** dans `profile.json` rabat automatiquement tout modèle `pro` vers **`flash`** *(jamais `inherit`, afin d'éviter la facturation Neon supérieure)*.
- **`"model_overrides"`** permet de surcharger individuellement `plan_model`, `build_model`, `review_model`, `eval_candidate` ou `eval_judge` parmi `{"inherit", "flash_lite", "flash", "pro"}` via `--plan-model`, `--build-model`, `--review-model` (`reset` pour effacer).
- Les `Args` passés explicitement à `review_pipeline.py` ou `run_suite.py` restent prioritaires sur le preset.

---

## Protocole A/B attribution des modèles (avec `gravity-metrics`)

Chaque run de `review_pipeline.py` et chaque fin de session `Stop` journalisent **`preset_profile`** (`quick` | `standard` | `deep`) et **`role_models`** effectifs (`{"plan": ..., "build": ..., "review": ...}`) dans `live_state.json` et `runs.jsonl` centralisés (`$GRAVITY_HOME/workflow/workspaces/<hash>/`).

Pour comparer objectivement l'impact coût/qualité entre deux configurations de modèles (ex. `build="flash"` vs `build="pro"`, ou `plan="flash"` vs `plan="pro"`) :

1. **Bras A (baseline) :** conserver le profil cible (`standard` ou `deep`) sans surcharge :
   ```bash
   python3 <plugin_root>/observability/profile_engine.py set --workspace . --mode standard --plan-model reset --build-model reset --review-model reset --json
   ```
2. **Bras B (variante testée) :** fixer une surcharge ciblée sur un seul rôle à la fois :
   ```bash
   python3 <plugin_root>/observability/profile_engine.py set --workspace . --mode deep --build-model pro --json
   ```
   *(Ou passer `Args={"plan_model": "...", "build_model": "...", "review_model": "..."}` sur `run_workflow` pour un essai ponctuel.)*
3. **Comparaison :** archiver et comparer les sessions via **`gravity-metrics`** (`/gravity-metrics` / `scripts/cli.py inspect`) croisé avec `runs.jsonl` (`preset_profile`, `role_models`, `verdict`, `remediation_rounds`) : mesurer taux de `PASS` au 1er passage, nombre de remédiations, tokens totaux et coût par tâche résolue avant de modifier les défauts de `LEVEL_PRESETS`.

---

## Profil centralisé & adaptation consultative `evo`

Le schéma par défaut (`schema_version: 2`) démarre avec `mode: "evo"`, `current_level: "standard"`, `locked: false`, `suggested_profile: "standard"`, `caveman: "lite"`, `caveman_locked: false`, `last_score: 50`.

En fin de session (`Stop`), [`stop_session_logger.py`](../plugin/hooks/stop_session_logger.py) s'exécute sur **tous les dépôts** (sans nécessiter `/gravity-workflow init`) et appelle `evolve_on_stop()` *(idempotent par `(conversation_id, execution_num)`, ignoré si `fully_idle: false`)* :

1. **Collecte comportementale uniquement** *(zéro comptage de fichiers `< 80`, zéro chute vers `quick`/`flash_lite`)* : rejets `Reviewer` récents (`live_state.json` + `runs.jsonl`), erreurs outils/tests ou arrêt anormal (`NORMAL_TERMINATION_REASONS`), refus `guard_deny_recent` postérieurs à `last_updated`, et taux `eval_pass_rate`.
2. **Recommandation consultative (`suggested_profile` : `standard` | `deep`) :** toute friction récente (`reviewer_rejections_recent > 0`, `guard_deny_recent > 0`, erreur outil/test/session, ou `eval_pass_rate < 1.0`) oriente `suggested_profile` vers **`deep`** avec le motif explicite dans `last_reason`, `status` et `live_state.json`. En régime sain, `suggested_profile` vaut **`standard`**.
3. **Zéro mutation silencieuse :** `evo` ne modifie jamais `current_level` ni `locked` tout seul ; l'utilisateur reste maître du changement de profil (`/gravity-workflow deep` ou `/gravity-workflow standard`).

---

## Skill compagnon `/caveman`

La concision est pilotée séparément par [`/caveman`](../plugin/skills/caveman/SKILL.md) sans modifier les profils :

- **Défaut sur les 3 profils :** **`lite`** (`caveman_locked: false`). Le passage automatique à `full` sur contexte/coût élevé sain est réservé à la Phase 3.
- **Commandes `/caveman` :** `/caveman` *(passe en `full` ou affiche l'état si déjà `full`)*, `/caveman lite|full|ultra|off` *(verrouille `caveman_locked: true`)*, `/caveman auto` *(alias `evo`, déverrouille et rétablit `lite`)*, `/caveman status`, `/caveman commit`, `/caveman compress <fichier>`.
- **Matrice ROI par cible (`build_subagent_directive`) :** chat principal (`off`/`lite`/`full`/`ultra` avec auto-clarté sur alertes `PreToolUse`, confirmations et `/grill-me`), `planner` plafonné à `full`, `reviewer` 1 ligne *Preuve ou Silence*, `builder` / `swarm` secs, `evals/` toujours `"off"`.
