---
trigger: manual
description: Checklist de pré-publication invoquée explicitement avant toute release (mention @rule en App/IDE ; lecture directe en CLI agy).
---

- [ ] `python3 <plugin_gravity>/scripts/validate.py --workspace . --strict` passe sans erreur ni avertissement.
- [ ] Suite `/gravity evals` exécutée (12/12 PASS) et checklist `[manual]` arbitrée par un humain.
- [ ] Conventions Git & versioning vérifiées par le chef d'orchestre (`.agents/rules/05-git-versioning.md` : branche hors `main`, SemVer, `CHANGELOG.md`, *Conventional Commits*, tag annoté `vX.Y.Z`).
