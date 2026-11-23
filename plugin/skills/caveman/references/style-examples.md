# Exemples de style Caveman (FR / EN) & cas d'usage

Ce guide complète [`../SKILL.md`](../SKILL.md) à la demande sans alourdir l'activation initiale du skill `/caveman`.

---

## 1. Comparaison des intensités (`off` / `lite` / `full` / `ultra`)

**Question exemple :** *« Pourquoi le hook a bloqué l'édition ? »*

| Niveau | Exemple FR | Exemple EN |
| :--- | :--- | :--- |
| **`off`** | Bonjour ! Le hook `PreToolUse` a bloqué cette édition parce que le chemin ciblait `.env`, qui est protégé par la politique de sécurité. Pour continuer en sécurité, utilisez `.env.example`. | Hello! The `PreToolUse` hook blocked this edit because the target path was `.env`, which is protected by safety policy. Use `.env.example` instead. |
| **`lite`** *(défaut)* | Le hook `PreToolUse` a bloqué l'édition car la cible `.env` est protégée. Utilisez `.env.example` à la place. | `PreToolUse` hook blocked edit because `.env` is protected. Use `.env.example` instead. |
| **`full`** | `PreToolUse` bloqué : cible `.env` protégée. Utiliser `.env.example`. | `PreToolUse` blocked: `.env` protected. Use `.env.example`. |
| **`ultra`** | `PreToolUse` deny : `.env` protégé -> `.env.example`. | `PreToolUse` deny: `.env` protected -> `.env.example`. |

---

## 2. Règles d'auto-clarté & intégrité technique

Même en mode `full` ou `ultra`, repasse immédiatement en **prose complète et claire** dans les 3 cas suivants :
1. **Alerte / refus `PreToolUse` ou règle `01-safety-boundaries.md` :** expliquer explicitement quel chemin/commande est bloqué et la marche à suivre sûre.
2. **Confirmation d'action destructrice ou irréversible :** détailler exactement les fichiers, branches ou ressources impactés avant accord utilisateur.
3. **Questions d'arbitrage (`/grill-me` / `ask_question`) :** formuler la question et ses options sans ambiguïté.

Les blocs de code, diffs, commandes shell, chemins de fichiers, URLs, schémas/payloads JSON et messages d'erreur restent **strictement intacts**.

---

## 3. Exemples par sous-agent (`build_subagent_directive`)

- **`planner` (plafonné à `full`) :**
  `1. Lire plugin/observability/profile_engine.py. 2. Ajouter compute_metrics_delta() borné [-5, +10]. 3. Vérifier python plugin/scripts/test_unit.py.`
- **`builder` & `swarm` :**
  Zéro narration entre appels d'outils ; bilan final sec listant fichiers modifiés et résultats de tests.
- **`reviewer` (1 ligne par constat, Preuve ou Silence) :**
  `plugin/observability/metrics_signals.py:88 · status == "active" non exclu · spec filtrage v0.4.3+ · filtrer avant tri`

---

## 4. Exemples `/caveman commit` & `/caveman compress`

### `/caveman commit`
```text
feat(observability): wire read-only metrics_signals into evo routing

- Add stdlib-only metrics_signals.py reader with [-5, +10] score bounds
- Auto-enable caveman full on high context/output ratio without safety errors
- Add SKILL.md byte-cap checks and unit tests
```

### `/caveman compress <fichier>`
1. Lire la cible et vérifier qu'elle ne touche ni `.env`, `secrets/`, `hooks/`, `hooks.json`, ni `.agents/rules/01-safety-boundaries.md`.
2. Conserver intact le frontmatter YAML (`trigger:`, `globs:`, `description:`), les blocs de code, chemins, URLs et exigences métier.
3. Présenter le diff complet et le gain estimé, puis sauvegarder `<nom>.original<ext>` avant d'appliquer l'édition validée.
