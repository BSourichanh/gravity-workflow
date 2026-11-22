# Outils & connexions (Natif d'abord, MCP minimal)

## Objectif
Éliminer toute plomberie externe superflue en exploitant les capacités natives d'Antigravity (permissions, terminal sandbox, navigateur, outils fichiers) et en réservant `.agents/mcp_config.json` du projet aux seuls services externes indispensables.

## Matrice d'arbitrage Natif vs MCP

| Besoin | Primitive prioritaire | Usage recommandé |
| :--- | :--- | :--- |
| Inspection & tests web | `/browser` (sous-agent `browser`, sur Flash par défaut) | **App `v2.30+` (comptes perso)** : navigateur latéral intégré, onglets parallèles, pause/reprise de connexion, zéro Node.js. **CLI `agy`, IDE & Business/Enterprise** : pilote Google Chrome (`v144+`) via Chrome DevTools MCP (`npx`/Node.js). Ne pas installer de MCP Playwright/Puppeteer redondant. |
| Exécution shell | Terminal sandboxé (`run_command`, `execute_command`, `bash`, `shell`) | Protégé nativement par **OS Sandbox** (Linux namespaces, macOS `sandbox-exec`, Windows 11 24H2+ août 2026 AppContainer) et le **moteur de permissions** (`Deny > Ask > Allow`, presets `Request Review`, `Default`, `Auto`, `Turbo`) + garde-fou `PreToolUse` du plugin. |
| Lecture / édition / recherche | `view_file` / `read_file`, `write_to_file` / `write_file`, `replace_file_content` / `edit_file`, `multi_replace_file_content`, `grep_search` / `list_dir` / `find_by_name` | Outils déterministes natifs prioritaires (protégés par `PreToolUse`). |
| SaaS / APIs cloud externes | `.agents/mcp_config.json` | Réservé aux connecteurs distants réels du projet (Jira, Slack, Sentry, Linear, bases de données) ou aux **MCP Apps** (`com.google.antigravity/ui`). |

## Configuration & sécurité MCP (`mcp_config.json`)
- **Transports supportés :** `stdio` local (`command`, `args`, `env`, `cwd`) et **Streamable HTTP** (`serverUrl` ou `url`, `headers`). *L'ancien transport HTTP+SSE (`httpUrl`) n'est pas supporté.*
- **Chargement paresseux (Lazy loading) par défaut :** Antigravity écrit les schémas JSON dans `<app-data-dir>/mcp/<serverName>/<toolName>.json` et appelle l'outil via `call_mcp_tool` afin d'économiser le contexte. Active `"tools": {"<tool>": {"eager": true}}` uniquement pour les outils critiques fréquents, et restreins la surface via `enabledTools` / `disabledTools` et `timeoutSeconds` (défaut : 300s connexion / 180s appel).
- **Authentification & secrets :**
  - Ne jamais inscrire de jeton, mot de passe ou clé API en clair dans `.agents/mcp_config.json`.
  - Préférer **OAuth 2.0 PKCE** (CIMD `https://antigravity.google/oauth/client-metadata.json` ou DCR automatique) ou **`"authProviderType": "google_credentials"`** (ADC).
  - Pour les plugins distribuables, déclarer `mcpServers.<server>.variables` (dialogue de saisie sécurisé dans l'UI) plutôt que `${VAR}` brut, car l'Antigravity App graphique (notamment sur macOS depuis le Dock) n'hérite pas toujours des variables d'environnement du shell utilisateur.
  - Si aucun service externe n'est requis, conserver `{"mcpServers": {}}`.

## Livrables produits dans le projet
- Matrice des outils retenus et preset de permissions documentés dans `.agents/gravity-workflow/PROGRESS.md`.
- `.agents/mcp_config.json` minimal, valide et dépourvu de tout secret en dur.

## Checkpoint déterministe
- Aucun serveur MCP ne duplique `/browser`, le terminal ou les outils fichiers natifs.
- `.agents/mcp_config.json` est un JSON valide contenant `mcpServers` sans secret en clair ni `httpUrl` obsolète.
- Mettre à jour `.agents/gravity-workflow/PROGRESS.md` et afficher la micro-carte.
