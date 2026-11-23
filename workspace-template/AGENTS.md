# Charte Projet — Agents Antigravity

## Identité & Livrable
- **Domaine :** <Décrire le projet et son processus répétitif cible>
- **Livrable principal :** <PR / Patch / Rapport structuré / Audit JSON>
- **Sources de vérité :** <Chemins vers les specs, schémas ou docs de référence>

## Règles de travail
1. Toujours respecter `.agents/rules/01-safety-boundaries.md` (`trigger: always_on`, préfixe statique conservé pour optimiser le Context Caching Gemini).
2. Ne jamais écraser un fichier existant sans diff préalable validé par l'utilisateur.
3. Privilégier les outils natifs (`/browser`, terminal sandbox, outils fichiers) avant tout connecteur MCP.
4. Soumettre tout livrable significatif au sous-agent `reviewer` (indépendant, à froid) avant clôture.
5. En cas de correction manuelle ou d'incident, appliquer la double boucle `/learn` : proposer une règle ciblée (`learning_proposal.md` validé avant écriture) et ajouter un cas de non-régression dans `.agents/gravity-workflow/evals/cases.json`.
6. **Git & versioning (chef d'orchestre uniquement) :** avant tout `git commit`, `git push` ou release, l'agent principal applique `.agents/rules/05-git-versioning.md` (branches hors `main`, *Conventional Commits*, SemVer, `CHANGELOG.md`, tags annotés `vX.Y.Z`) ; les sous-agents et workers `swarm` ne committent ni ne poussent jamais.
7. **Ingénierie & sobriété :** appliquer **KISS, YAGNI, DRY, SOLID, CUPID** (critères détaillés chargés à la demande via `.agents/rules/06-engineering-principles.md` en conception, refactor ou revue).
