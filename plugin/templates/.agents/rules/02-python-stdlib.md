---
trigger: glob
globs: '**/*.py'
description: Conventions Python 3 applicables aux scripts du projet et du harnais.
---

- Utiliser exclusivement la bibliothèque standard Python 3 (zéro dépendance externe non approuvée).
- Garantir un code de retour `0` et une sortie JSON conforme sur `stdout` (`{}` strict pour `PostToolUse` et `Stop`) pour tout script de hook.
- Dans les hooks, résoudre les chemins workspace depuis le payload `stdin` (`cwd` / `workspacePaths`).
