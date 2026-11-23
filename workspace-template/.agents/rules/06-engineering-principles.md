---
trigger: model_decision
description: Critères vérifiables KISS, YAGNI, DRY, SOLID et CUPID à charger lors de toute conception, création de module/script, refactorisation ou revue.
---

# Principes d'ingénierie (KISS, YAGNI, DRY, SOLID, CUPID)

1. **KISS :** choisir la solution la plus simple qui couvre le besoin prouvé ; rejeter toute couche ou abstraction non justifiée.
2. **YAGNI :** ne générer ni configurer aucune option, règle spécifique de langage ou fonctionnalité spéculative « au cas où ».
3. **DRY :** maintenir une source de vérité unique par donnée, template ou règle (ne pas dupliquer des fichiers de template en constantes).
4. **SOLID :** séparer logique métier, I/O et interface CLI (responsabilité unique) ; privilégier des contrats petits et extensibles sans casser l'existant.
5. **CUPID :** concevoir des unités composables, prévisibles, idiomatiques et centrées domaine, testables isolément.
