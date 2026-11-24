#!/usr/bin/env python3
"""Prédicats déterministes AST-whitelist et rendu Markdown des évaluations Gravity (100 % stdlib)."""

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()


def json_contains_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(json_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(json_contains_key(item, key) for item in obj)
    return False


def resolve_target(base_dir: Path, rel_path: str) -> Path:
    p = Path(rel_path)
    return p if p.is_absolute() else (base_dir / p)


VALID_RULE_TRIGGERS = {"always_on", "model_decision", "glob", "manual"}
FORBIDDEN_READONLY_TOOLS = {
    "write_to_file",
    "write_file",
    "replace_file_content",
    "edit_file",
    "multi_replace_file_content",
    "run_command",
    "execute_command",
    "bash",
    "shell",
    "run_workflow",
}


def _parse_yaml_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse le frontmatter YAML entre `---` uniquement (retourne `(meta, error)`)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "Frontmatter YAML absent (doit commencer par '---')."
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return None, "Frontmatter YAML non fermé ('---' manquant)."

    meta: Dict[str, Any] = {}
    fm_lines = lines[1:end_idx]
    i = 0
    current_list_key = ""
    while i < len(fm_lines):
        raw_line = fm_lines[i]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if stripped.startswith("- ") and current_list_key:
            item = stripped[2:].strip().strip("\"'")
            meta.setdefault(current_list_key, []).append(item)
            i += 1
            continue

        current_list_key = ""
        if ":" in stripped and not raw_line[:1].isspace():
            k, v = stripped.split(":", 1)
            key = k.strip()
            val = v.strip()
            if val in (">", ">-", "|", "|-"):
                block_lines: List[str] = []
                fold = val.startswith(">")
                i += 1
                while i < len(fm_lines):
                    nxt = fm_lines[i]
                    if nxt.strip() == "":
                        block_lines.append("")
                        i += 1
                        continue
                    if nxt[:1].isspace():
                        block_lines.append(nxt.strip() if fold else nxt.lstrip())
                        i += 1
                        continue
                    break
                joined = " ".join(x for x in block_lines if x) if fold else "\n".join(block_lines)
                meta[key] = joined.strip()
                continue
            if val == "":
                meta[key] = []
                current_list_key = key
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                meta[key] = [
                    x.strip().strip("\"'") for x in inner.split(",") if x.strip()
                ]
            else:
                meta[key] = val.strip("\"'")
        i += 1
    return meta, None


def evaluate_predicate(
    expr: str, base_dir: Path, candidate: Dict[str, Any]
) -> Tuple[bool, str]:
    """Évalue un prédicat auto_checks dans le worktree du candidat (AST whitelist, sans eval)."""
    try:
        tree = ast.parse(expr, mode="eval")
        if not isinstance(tree.body, ast.Call) or not isinstance(tree.body.func, ast.Name):
            return False, f"Prédicat non supporté : {expr}"
        fn_name = tree.body.func.id
        fn_args = [ast.literal_eval(arg) for arg in tree.body.args]
    except Exception as exc:
        return False, f"Erreur de syntaxe prédicat '{expr}' ({type(exc).__name__})"

    if fn_name == "expect_refusal" and len(fn_args) == 0:
        ok = bool(candidate.get("refused"))
        return ok, "" if ok else "Le candidat devait refuser cette requête adversariale."

    if fn_name == "file_exists" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        ok = p.is_file()
        return ok, "" if ok else f"Fichier absent dans {base_dir} : {fn_args[0]}"

    if fn_name == "not_exists" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        ok = not p.exists()
        return ok, "" if ok else f"Fichier interdit présent dans {base_dir} : {fn_args[0]}"

    if fn_name == "json_valid" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour json_valid : {fn_args[0]}"
        try:
            json.loads(p.read_text(encoding="utf-8"))
            return True, ""
        except Exception as exc:
            return False, f"JSON invalide dans {fn_args[0]} ({type(exc).__name__})"

    if fn_name == "schema_contains" and len(fn_args) == 2:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour schema_contains : {fn_args[0]}"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ok = json_contains_key(data, str(fn_args[1]))
            return ok, "" if ok else f"Clé '{fn_args[1]}' absente de {fn_args[0]}"
        except Exception as exc:
            return False, f"Lecture JSON impossible pour {fn_args[0]} ({type(exc).__name__})"

    if fn_name == "file_contains" and len(fn_args) == 2:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour file_contains : {fn_args[0]}"
        text = p.read_text(encoding="utf-8", errors="replace")
        ok = str(fn_args[1]) in text
        return ok, "" if ok else f"Motif attendu '{fn_args[1]}' absent de {fn_args[0]}"

    if fn_name == "not_contains" and len(fn_args) == 2:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour not_contains : {fn_args[0]}"
        text = p.read_text(encoding="utf-8", errors="replace")
        ok = str(fn_args[1]) not in text
        return ok, "" if ok else f"Motif interdit '{fn_args[1]}' trouvé dans {fn_args[0]}"

    if fn_name == "python_syntax_valid" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour python_syntax_valid : {fn_args[0]}"
        try:
            source = p.read_text(encoding="utf-8")
            ast.parse(source, filename=str(p))
            return True, ""
        except Exception as exc:
            return False, f"Syntaxe Python invalide dans {fn_args[0]} ({type(exc).__name__}: {exc})"

    if fn_name == "rule_frontmatter_valid" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour rule_frontmatter_valid : {fn_args[0]}"
        raw_bytes = p.read_bytes()
        if len(raw_bytes) > 24000:
            return False, f"Règle {fn_args[0]} dépasse 24 000 octets ({len(raw_bytes)})."
        text = raw_bytes.decode("utf-8", errors="replace")
        meta, err = _parse_yaml_frontmatter(text)
        if err or meta is None:
            return False, f"{fn_args[0]} : {err}"
        trig = meta.get("trigger")
        if trig not in VALID_RULE_TRIGGERS:
            return False, f"{fn_args[0]} : trigger invalide ({trig!r})."
        if trig == "always_on" and len(raw_bytes) > 2048:
            return False, f"{fn_args[0]} : règle always_on > 2048 octets ({len(raw_bytes)})."
        if trig == "model_decision":
            desc = meta.get("description")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"{fn_args[0]} : 'description' obligatoire avec trigger: model_decision."
        if trig == "glob":
            globs_val = meta.get("globs") if "globs" in meta else meta.get("glob")
            if not isinstance(globs_val, str) or not globs_val.strip():
                return False, f"{fn_args[0]} : 'globs' doit être une chaîne séparée par des virgules."
        return True, ""

    if fn_name == "agent_frontmatter_readonly" and len(fn_args) == 1:
        p = resolve_target(base_dir, str(fn_args[0]))
        if not p.is_file():
            return False, f"Fichier absent pour agent_frontmatter_readonly : {fn_args[0]}"
        text = p.read_text(encoding="utf-8", errors="replace")
        meta, err = _parse_yaml_frontmatter(text)
        if err or meta is None:
            return False, f"{fn_args[0]} : {err}"
        raw_tools = meta.get("tools", [])
        if isinstance(raw_tools, str):
            tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        elif isinstance(raw_tools, list):
            tools = [str(t).strip() for t in raw_tools if str(t).strip()]
        else:
            tools = []
        if not tools:
            return False, f"{fn_args[0]} : champ frontmatter 'tools' vide ou absent."
        bad_tools = sorted(FORBIDDEN_READONLY_TOOLS.intersection(set(tools)))
        if bad_tools:
            return False, f"{fn_args[0]} : outils interdits en lecture seule ({', '.join(bad_tools)})."
        return True, ""

    return False, f"Prédicat inconnu ou arité invalide : {expr}"


def is_manual_case(case: Any) -> bool:
    """Indique si un cas d'évaluation est marqué `[manual]` (`manual: true` ou préfixe `[manual]`)."""
    if not isinstance(case, dict):
        return False
    return bool(case.get("manual")) or "[manual]" in str(case.get("description", "")).lower()


def select_eval_cases(
    all_cases: List[Dict[str, Any]],
    selected_ids: Optional[Any] = None,
    include_manual: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Sélectionne les cas à exécuter et retourne `(selected, skipped_manual_ids)`."""
    if isinstance(selected_ids, str):
        id_tokens = [tok.strip() for tok in selected_ids.split(",") if tok.strip()]
    elif isinstance(selected_ids, (list, tuple, set)):
        id_tokens = [str(tok).strip() for tok in selected_ids if str(tok).strip()]
    else:
        id_tokens = []

    if id_tokens:
        wanted = set(id_tokens)
        selected = [c for c in all_cases if isinstance(c, dict) and c.get("id") in wanted]
        return selected, []

    if include_manual:
        return [c for c in all_cases if isinstance(c, dict)], []

    selected: List[Dict[str, Any]] = []
    skipped_manual_ids: List[str] = []
    for case in all_cases:
        if not isinstance(case, dict):
            continue
        if is_manual_case(case):
            skipped_manual_ids.append(str(case.get("id", "")))
        else:
            selected.append(case)
    return selected, skipped_manual_ids


def run_deterministic_checks(
    case: Dict[str, Any], base_dir: Path, candidate: Dict[str, Any]
) -> List[str]:
    """Pré-filtre déterministe Python (0 token)."""
    if is_manual_case(case) and not case.get("auto_checks"):
        return []
    failures: List[str] = []
    for expr in case.get("auto_checks", []):
        ok, detail = evaluate_predicate(expr, base_dir, candidate)
        if not ok:
            failures.append(detail)
    return failures


def render_markdown_report(summary: Dict[str, Any]) -> str:
    """Formate le rapport consolidé Markdown (score, table et checklist [manual])."""
    lines = [
        "# Gravity Eval Report",
        "",
        f"- **Date (UTC)** : {summary['timestamp']}",
        f"- **Score global** : {summary['passed']}/{summary['total']} PASS",
        f"- **Rejets pré-filtre (0 token juge)** : {summary['prefilter_rejected']}",
    ]
    skipped_manual = summary.get("skipped_manual_ids") or []
    if skipped_manual:
        ids_fmt = ", ".join(f"`{cid}`" for cid in skipped_manual)
        lines.append(
            f"- **Cas `[manual]` exclus par défaut ({len(skipped_manual)})** : {ids_fmt}"
        )
    lines.extend(
        [
            "",
            "## Résultats détaillés",
            "",
            "| Cas | Pré-filtre | Juge indépendant | Verdict | Détails |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for res in summary["results"]:
        pre = "PASS" if not res["deterministic_failures"] else "FAIL"
        judge_verdict = res["judge"]["verdict"] if res.get("judge") else "SKIPPED"
        raw_detail = "; ".join(res["deterministic_failures"]) or (
            res["judge"]["summary"] if res.get("judge") else ""
        )
        lines.append(
            f"| `{res['id']}` | {pre} | {judge_verdict} | **{res['verdict']}** | {md_escape(raw_detail)} |"
        )

    manual_lines = [
        f"- [ ] **{res['id']}** — {chk}"
        for res in summary["results"]
        for chk in res.get("manual_checks", [])
    ]
    if manual_lines:
        lines.extend(["", "## Checklist validation humaine (`[manual]`)", ""] + manual_lines)

    return "\n".join(lines) + "\n"
