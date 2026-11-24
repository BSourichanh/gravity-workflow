---
trigger: manual
description: Checklist de pré-publication invoquée explicitement avant toute release (mention @rule en App/IDE ; lecture directe en CLI agy).
---

- [ ] `python3 <plugin_gravity_workflow>/scripts/validate.py --workspace . --strict` (ou `python` sous Windows) passe sans erreur ni avertissement.
- [ ] Suite `/gravity-workflow evals` exécutée (`100 % PASS` auto, plus cas `[manual]` rejoués si applicables) et checklist `[manual]` arbitrée par un humain.
- [ ] Synchronisation vérifiée : `CHANGELOG.md`, documentation, templates et liens valides, encodage UTF-8 sans BOM et fins de ligne LF.
- [ ] Conventions Git & versioning vérifiées par le chef d'orchestre (`.agents/rules/05-git-versioning.md` : branche hors `main`, SemVer, `CHANGELOG.md`, *Conventional Commits*, tag annoté `vX.Y.Z`).
