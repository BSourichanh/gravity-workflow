---
trigger: model_decision
description: Conventions Git et versioning (branches, Conventional Commits, SemVer, CHANGELOG, tags) réservées au chef d'orchestre (agent principal) avant tout commit, push ou release — ignorées par les sous-agents et workers swarm.
---

# Conventions Git & Versioning (Chef d'orchestre uniquement)

> **Portée stricte :** Cette règle s'applique **exclusivement au chef d'orchestre** (agent principal / session `/gravity`). Les sous-agents (`planner`, `builder`, `reviewer`) et les workers `swarm` ne doivent **jamais** exécuter `git commit`, `git push`, `git tag`, ni modifier les numéros de version ou `CHANGELOG.md` à titre de release.

Avant tout `git commit` et `git push` (toujours après demande ou validation explicite de l'utilisateur) :

1. **Stratégie de branches :**
   - Travailler sur `dev` ou une branche dédiée (`feat/<nom>`, `fix/<nom>`, `docs/<nom>`, `chore/<nom>`).
   - **Jamais** de commit ni de push direct sur `main` ou `master` (intégration uniquement via `dev` / PR / merge contrôlé ; `--force` / `-f` / `+ref` strictement interdits).
2. **Propreté & vérifications pré-commit / pré-push :**
   - Contrôler `git status` et `git diff --staged` : exclure tout secret (`.env`, clés), fichier temporaire ou artefact local (`__pycache__/`, `*.lock`, `runs.jsonl`).
   - En mode **`safe`** (défaut) : exécuter les validations et tests du projet avant de commiter et pousser ; en mode **`unsafe`**, vérifier au minimum la propreté du diff et l'absence de secrets.
3. **Messages *Conventional Commits* :**
   - Titre impératif `<= 50` car. : `<type>(<scope>): <résumé>` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`, `style` ; ajouter `!` et un pied `BREAKING CHANGE:` en cas de rupture).
   - Privilégier le **pourquoi** dans le corps si des puces complémentaires (`<= 72` car./ligne) sont nécessaires.
4. **Semantic Versioning (`MAJOR.MINOR.PATCH`) & `CHANGELOG.md` :**
   - Déterminer si le lot requiert une montée SemVer (`MAJOR` rupture, `MINOR` ajout rétrocompatible, `PATCH` correctif) et synchroniser tous les manifestes de version du projet (`plugin.json`, `package.json`, `pyproject.toml`, `Cargo.toml`, `VERSION`, etc.).
   - Mettre à jour `CHANGELOG.md` au format *Keep a Changelog* (`[Unreleased]` ou `[X.Y.Z] - YYYY-MM-DD`, rubriques `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`) pour tout changement notable ou release.
5. **Tags Git annotés (`vX.Y.Z`) :**
   - À chaque montée de version / release validée, créer un tag annoté aligné avec le manifeste et `CHANGELOG.md` (`git tag -a vX.Y.Z -m "Release vX.Y.Z"`), puis pousser explicitement le tag (`git push origin vX.Y.Z` ou `--follow-tags`, sans jamais forcer l'écrasement d'un tag existant).
