"""Parsing and item-by-item validation of workbench input."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_FRAGMENTS = 4
MAX_FRAGMENTS = 48
MAX_WEIGHT = 1_000_000_000


class ValidationError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("input validation failed")
        self.errors = errors


@dataclass
class ParsedInput:
    fragment_ids: list[int]
    conflicts: list[tuple[int, int]]
    stitches: list[tuple[int, int, int]]
    conflict_lines: dict[tuple[int, int], int] = field(default_factory=dict)
    stitch_lines: dict[tuple[int, int], int] = field(default_factory=dict)


def _strip_line(line: str) -> list[str]:
    """Split one input line into tokens, accepting common separators.

    Accepted shapes include ``1 2``, ``1-2``, ``1, 2`` and ``1-2: 5``.
    Text after a ``#`` is treated as a comment.
    """
    line = line.split("#", 1)[0]
    for ch in ":=()[]":
        line = line.replace(ch, " ")
    # A dash joining two numeric endpoints ("1-2" or "1 - 2") is an edge
    # separator; a dash directly attached to the following number ("-4",
    # "1 -4") is a unary minus and must survive so the value can be rejected
    # (fragments/weights must be positive).
    line = re.sub(r"(?<=\d)\s+-\s+(?=\d)", " ", line)
    line = re.sub(r"(?<=\d)-\s+(?=\d)", " ", line)
    line = re.sub(r"(?<=\d)-(?=\d)", " ", line)
    return [t for t in re.split(r"[\s,;|]+", line.strip()) if t]


def _err(code: str, message: str, field_name: str, line: int | None,
         raw: str | None) -> dict:
    e = {"code": code, "message": message, "field": field_name}
    if line is not None:
        e["line"] = line
    if raw is not None:
        e["raw"] = raw
    return e


def _parse_int(token: str) -> int | None:
    if re.fullmatch(r"[+-]?\d+", token):
        try:
            return int(token)
        except ValueError:
            return None
    return None


def parse_input(fragments_text: str, conflicts_text: str,
                stitches_text: str) -> ParsedInput:
    errors: list[dict] = []

    # ---- fragments -------------------------------------------------------
    fragment_ids: list[int] = []
    seen_ids: set[int] = set()
    for lineno, raw in enumerate(fragments_text.splitlines(), start=1):
        tokens = _strip_line(raw)
        if not tokens:
            continue
        for tok in tokens:
            val = _parse_int(tok)
            if val is None:
                errors.append(_err(
                    "fragment_not_integer",
                    f"片段编号必须是整数，无法识别 “{tok}”",
                    "fragments", lineno, raw.strip()))
                continue
            if val < 1:
                errors.append(_err(
                    "fragment_not_positive",
                    f"片段编号必须为正整数，得到 {val}",
                    "fragments", lineno, raw.strip()))
                continue
            if val in seen_ids:
                errors.append(_err(
                    "fragment_duplicate",
                    f"片段 {val} 重复声明",
                    "fragments", lineno, raw.strip()))
                continue
            seen_ids.add(val)
            fragment_ids.append(val)

    if not errors:
        if len(fragment_ids) < MIN_FRAGMENTS:
            errors.append(_err(
                "too_few_fragments",
                f"模型至少需要 {MIN_FRAGMENTS} 个唯一片段，"
                f"当前只有 {len(fragment_ids)} 个",
                "fragments", None, None))
        elif len(fragment_ids) > MAX_FRAGMENTS:
            errors.append(_err(
                "too_many_fragments",
                f"模型至多包含 {MAX_FRAGMENTS} 个唯一片段，"
                f"当前有 {len(fragment_ids)} 个",
                "fragments", None, None))

    # ---- conflict edges --------------------------------------------------
    conflicts: list[tuple[int, int]] = []
    conflict_lines: dict[tuple[int, int], int] = {}
    for lineno, raw in enumerate(conflicts_text.splitlines(), start=1):
        tokens = _strip_line(raw)
        if not tokens:
            continue
        if len(tokens) != 2:
            errors.append(_err(
                "conflict_bad_arity",
                f"冲突边需要恰好两个片段编号，当前得到 {len(tokens)} 项",
                "conflicts", lineno, raw.strip()))
            continue
        a, b = (_parse_int(t) for t in tokens)
        if a is None or b is None:
            bad = tokens[0] if a is None else tokens[1]
            errors.append(_err(
                "conflict_not_integer",
                f"端点编号必须是整数，无法识别 “{bad}”",
                "conflicts", lineno, raw.strip()))
            continue
        if a not in seen_ids or b not in seen_ids:
            missing = a if a not in seen_ids else b
            errors.append(_err(
                "endpoint_undeclared",
                f"端点 {missing} 未在片段列表中声明",
                "conflicts", lineno, raw.strip()))
            continue
        if a == b:
            errors.append(_err(
                "self_loop",
                f"冲突边不能形成自环：{a}-{a}",
                "conflicts", lineno, raw.strip()))
            continue
        key = (min(a, b), max(a, b))
        if key in conflict_lines:
            errors.append(_err(
                "duplicate_conflict_edge",
                f"冲突边 {key[0]}-{key[1]} 与第 "
                f"{conflict_lines[key]} 行重复，同一无向点对不得重复",
                "conflicts", lineno, raw.strip()))
            continue
        conflict_lines[key] = lineno
        conflicts.append(key)

    # ---- stitch edges ----------------------------------------------------
    stitches: list[tuple[int, int, int]] = []
    stitch_lines: dict[tuple[int, int], int] = {}
    for lineno, raw in enumerate(stitches_text.splitlines(), start=1):
        tokens = _strip_line(raw)
        if not tokens:
            continue
        if len(tokens) != 3:
            errors.append(_err(
                "stitch_bad_arity",
                "缝合边需要两个端点和一个正整数权重（例如 1-2: 5），"
                f"当前得到 {len(tokens)} 项",
                "stitches", lineno, raw.strip()))
            continue
        a, b = _parse_int(tokens[0]), _parse_int(tokens[1])
        w = _parse_int(tokens[2])
        if a is None or b is None or w is None:
            bad = next(t for t, v in zip(tokens, (a, b, w)) if v is None)
            errors.append(_err(
                "stitch_not_integer",
                f"端点与权重必须是整数，无法识别 “{bad}”",
                "stitches", lineno, raw.strip()))
            continue
        if a not in seen_ids or b not in seen_ids:
            missing = a if a not in seen_ids else b
            errors.append(_err(
                "endpoint_undeclared",
                f"端点 {missing} 未在片段列表中声明",
                "stitches", lineno, raw.strip()))
            continue
        if a == b:
            errors.append(_err(
                "self_loop",
                f"缝合边不能形成自环：{a}-{a}",
                "stitches", lineno, raw.strip()))
            continue
        if w < 1:
            errors.append(_err(
                "weight_not_positive",
                f"缝合边权重必须为正整数，得到 {w}",
                "stitches", lineno, raw.strip()))
            continue
        if w > MAX_WEIGHT:
            errors.append(_err(
                "weight_too_large",
                f"缝合边权重不得超过 {MAX_WEIGHT}，得到 {w}",
                "stitches", lineno, raw.strip()))
            continue
        key = (min(a, b), max(a, b))
        if key in stitch_lines:
            errors.append(_err(
                "duplicate_stitch_edge",
                f"缝合边 {key[0]}-{key[1]} 与第 "
                f"{stitch_lines[key]} 行重复，同一无向点对不得重复",
                "stitches", lineno, raw.strip()))
            continue
        if key in conflict_lines:
            errors.append(_err(
                "edge_type_conflict",
                f"点对 {key[0]}-{key[1]} 已在第 {conflict_lines[key]} 行"
                "声明为冲突边，不能同时属于缝合边",
                "stitches", lineno, raw.strip()))
            continue
        stitch_lines[key] = lineno
        stitches.append((key[0], key[1], w))

    if errors:
        raise ValidationError(errors)

    return ParsedInput(
        fragment_ids=sorted(fragment_ids),
        conflicts=conflicts,
        stitches=stitches,
        conflict_lines=conflict_lines,
        stitch_lines=stitch_lines,
    )
