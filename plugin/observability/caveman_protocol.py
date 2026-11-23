#!/usr/bin/env python3
"""Protocole Caveman natif Gravity : intensités, adaptation evo et directives ciblées par ROI (100 % stdlib)."""

from typing import Any, Dict, Tuple

VALID_CAVEMAN_LEVELS: Tuple[str, ...] = ("off", "lite", "full", "ultra")
VALID_CAVEMAN_SETTINGS: Tuple[str, ...] = ("auto", "off", "lite", "full", "ultra")
CAVEMAN_ALIASES: Dict[str, str] = {"evo": "auto"}
DEFAULT_CAVEMAN = "lite"

_LEVEL_RULES: Dict[str, str] = {
    "lite": "zéro politesse, intro/outro ni remplissage ; garde phrases complètes claires (FR/EN).",
    "full": "style télégraphique sans articles/connecteurs superflus ; fragments précis (FR/EN).",
    "ultra": "densité maximale (abréviations techniques, causalité X -> Y, zéro blabla).",
}


def normalize_caveman(raw: str) -> str:
    """Valide et normalise un réglage Caveman (`auto`, `evo`, `off`, `lite`, `full`, `ultra`)."""
    cleaned = str(raw or "").strip().lower()
    cleaned = CAVEMAN_ALIASES.get(cleaned, cleaned)
    if cleaned not in VALID_CAVEMAN_SETTINGS:
        raise ValueError(
            f"Niveau Caveman invalide {raw!r} (attendu : {', '.join(VALID_CAVEMAN_SETTINGS)} ou 'evo')."
        )
    return cleaned


def effective_planner_caveman(level: str) -> str:
    """Plafonne Caveman pour le Planner à `full` (`ultra` -> `full`) afin d'éviter toute ambiguïté."""
    cleaned = str(level or "").strip().lower()
    if cleaned not in VALID_CAVEMAN_LEVELS or cleaned == "off":
        return "off"
    if cleaned == "ultra":
        return "full"
    return cleaned


def choose_evo_caveman(
    current_level: str,
    signals: Dict[str, Any],
    clean_streak: int,
) -> Tuple[str, str]:
    """Adapte le niveau Caveman evo (`lite` par défaut/sécurité, `full` auto si contexte/coût output élevé)."""
    sig = signals if isinstance(signals, dict) else {}
    eval_rate = sig.get("eval_pass_rate")
    eval_failing = eval_rate is not None and float(eval_rate) < 1.0
    guard_deny = int(sig.get("guard_deny_recent") or 0) > 0
    session_error = bool(sig.get("session_error"))

    if session_error or guard_deny or eval_failing:
        return "lite", "caveman lite (clarté prioritaire suite à erreur, alerte deny ou éval)"

    metrics = sig.get("metrics")
    if isinstance(metrics, dict) and metrics.get("available"):
        try:
            max_ctx = int(metrics.get("max_context_tokens") or 0)
            out_ratio = float(metrics.get("high_output_cost_ratio") or 0.0)
        except (TypeError, ValueError):
            max_ctx = 0
            out_ratio = 0.0
        if max_ctx >= 150_000 or out_ratio >= 0.35:
            return "full", "caveman full (contexte/output élevé mesuré par metrics)"

    return "lite", "caveman lite (défaut nominal sur tous les profils)"


def build_session_start_prompt(level: str, *, locked: bool = False) -> str:
    """Construit l'instruction Caveman (~100 tokens) injectée une fois en `SessionStart`."""
    cleaned = str(level or "").strip().lower()
    if cleaned not in VALID_CAVEMAN_LEVELS or cleaned == "off":
        return ""

    mode_tag = "verrouillé" if locked else "auto/evo"
    rule_text = _LEVEL_RULES[cleaned]
    return (
        f"[Gravity Caveman · niveau={cleaned} ({mode_tag})] "
        f"Style réponse : {rule_text} "
        "Code, diffs, commandes, chemins, URLs, JSON et erreurs strictement intacts. "
        "Auto-clarté obligatoire (prose complète claire) pour : alertes/refus PreToolUse, "
        "confirmations d'actions irréversibles et questions /grill-me / ask_question. "
        "Sous-agents : planner plafonné à full, reviewer sur 1 ligne (Preuve ou Silence), "
        "builder/swarm sans narration, evals exclus."
    )


def build_subagent_directive(role: str, level: str) -> str:
    """Retourne la directive Caveman adaptée au ROI d'un sous-agent (`planner`, `builder`, `reviewer`, `swarm`)."""
    cleaned_level = str(level or "").strip().lower()
    if cleaned_level not in VALID_CAVEMAN_LEVELS or cleaned_level == "off":
        return ""

    cleaned_role = str(role or "").strip().lower()
    if cleaned_role == "planner":
        eff = effective_planner_caveman(cleaned_level)
        if eff == "lite":
            return (
                "[Caveman:lite] Plan concis sans politesse ni remplissage "
                "(fichiers, invariants, vérifs exacts)."
            )
        return (
            "[Caveman:full] Plan télégraphique sans articles ni blabla "
            "(fichiers, invariants, vérifs exacts ; zéro ambiguïté)."
        )
    if cleaned_role == "builder":
        return (
            f"[Caveman:{cleaned_level}] Zéro narration entre outils ; "
            "code/diffs exacts ; summary sec et factuel."
        )
    if cleaned_role == "reviewer":
        return (
            f"[Caveman:{cleaned_level}] Constats sur 1 ligne "
            "(`chemin:ligne · preuve littérale · règle violée · fix`) ; "
            "respect strict de Preuve ou Silence et du verdict."
        )
    if cleaned_role == "swarm":
        return (
            f"[Caveman:{cleaned_level}] Messages Swarm télégraphiques et factuels "
            "sans politesse ; chemins et preuves exacts."
        )
    return ""
