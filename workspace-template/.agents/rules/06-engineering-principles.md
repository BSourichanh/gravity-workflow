---
trigger: model_decision
description: Critères vérifiables KISS, YAGNI, DRY, SOLID, CUPID et repères pragmatiques d'Architecture Hexagonale & DDD à charger lors de toute conception, création de module/script, refactorisation ou revue.
---

# Principes d'ingénierie (KISS, YAGNI, DRY, SOLID, CUPID)

1. **KISS :** choisir la solution la plus simple qui couvre le besoin prouvé ; rejeter toute couche ou abstraction non justifiée.
2. **YAGNI :** ne générer ni configurer aucune option, règle spécifique de langage ou fonctionnalité spéculative « au cas où ».
3. **DRY :** maintenir une source de vérité unique par donnée, template ou règle (ne pas dupliquer des fichiers de template en constantes).
4. **SOLID :** séparer logique métier, I/O et interface CLI (responsabilité unique) ; privilégier des contrats petits et extensibles sans casser l'existant.
5. **CUPID :** concevoir des unités composables, prévisibles, idiomatiques et centrées domaine, testables isolément.

## Architecture Hexagonale & DDD (pragmatique)

- **Langage ubiquitaire & *Bounded Context* :** nommer modules, types et contrats selon le vocabulaire métier du contexte ; expliciter toute frontière partagée (*Shared Kernel*, contrat JSON/SPI) sans fuite de détails internes.
- **Noyau domaine isolé des I/O :** garder les règles métier et invariants purs (sans appel direct réseau, filesystem, CLI ou MCP) afin qu'ils restent testables isolément sans mocks lourds.
- **Ports & Adapteurs :** définir des ports minces aux frontières (entrants : CLI, serveur MCP, hooks ; sortants : stockage, Git, APIs externes) et cantonner sérialisation, parsing et accès I/O dans les adapteurs.
- **Quand appliquer vs s'abstenir (KISS/YAGNI) :**
  - **Appliquer** dès qu'un composant porte des règles métier non triviales, plusieurs adapteurs I/O ou une frontière partagée (`SPI`, contrat public).
  - **S'abstenir** sur les scripts utilitaires linéaires, wrappers minces ou helpers mono-usage : ne jamais introduire de hiérarchie d'interfaces, ports ou *repositories* spéculative sans second adapteur ou besoin de test isolé avéré.
