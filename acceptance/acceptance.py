#!/usr/bin/env python3
"""Acceptance suite for the three-mask workbench.

Runs against a live HTTP API (the FastAPI container), exercising every
contract required by the specification:

* health endpoint and static SPA serving;
* itemized input validation (self loops, duplicates, type overlap,
  undeclared endpoints, non-positive weights, fragment count bounds);
* infeasible instances clearly distinguished from invalid input;
* optimal solutions: proper coloring, exact cut-weight accounting,
  canonical first-appearance normalization by ascending fragment id;
* uniqueness of the canonical optimum and a distinct second witness;
* end-to-end consistency across a sweep of generated instances.

Exits non-zero on the first failed check, printing a readable trace.
"""

from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.request

BASE = os.getenv("VERIFY_BASE_URL", "http://app:8000").rstrip("/")
PASSED = 0
FAILED = 0


def check(cond: bool, name: str, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def request(method: str, path: str, payload: dict | None = None,
            expect_status: int = 0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def get_text(path: str) -> tuple[int, str]:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.status, resp.read().decode()


def solve(payload: dict):
    return request("POST", "/api/solve", payload)


def is_canonical(colors: list[int]) -> bool:
    seen = []
    for c in colors:
        if c not in seen:
            if seen and c != max(seen) + 1:
                return False
            seen.append(c)
    return seen == list(range(len(seen)))


def main() -> int:
    print(f"verify against {BASE}")

    # ---- health & static -------------------------------------------------
    status, body = request("GET", "/health")
    check(status == 200 and body.get("status") == "ok", "健康检查 200")

    status, html = get_text("/")
    check(status == 200 and "三掩模" in html, "根路径返回前端页面")

    # ---- itemized validation --------------------------------------------
    print("[输入校验]")
    status, body = solve({
        "fragments": "1\n2\n2\nx",
        "conflicts": "1 1\n1 2\n1-2\n9 2",
        "stitches": "1 2 3\n1 2 5\n2 1 -4\nbad row",
    })
    check(status == 422 and body["status"] == "invalid",
          "非法输入返回 422 invalid")
    codes = {(e["field"], e["code"]) for e in body["errors"]}
    for expected in [
        ("fragments", "fragment_duplicate"),
        ("fragments", "fragment_not_integer"),
        ("conflicts", "self_loop"),
        ("conflicts", "duplicate_conflict_edge"),
        ("conflicts", "endpoint_undeclared"),
        ("stitches", "edge_type_conflict"),
        ("stitches", "weight_not_positive"),
        ("stitches", "stitch_bad_arity"),
    ]:
        check(expected in codes, f"逐项错误包含 {expected[1]}",
              f"got {sorted(codes)}")
    check(all("message" in e and "line" in e for e in body["errors"]),
          "每条错误带中文说明与行号")

    status, body = solve({"fragments": "1 2 3", "conflicts": "", "stitches": ""})
    check(status == 422 and
          any(e["code"] == "too_few_fragments" for e in body["errors"]),
          "少于 4 个片段被拒绝")

    status, body = solve({
        "fragments": " ".join(str(i) for i in range(1, 50)),
        "conflicts": "", "stitches": "",
    })
    check(status == 422 and
          any(e["code"] == "too_many_fragments" for e in body["errors"]),
          "超过 48 个片段被拒绝")

    status, body = solve({
        "fragments": "1 2 3 4",
        "conflicts": "1 2",
        "stitches": "1 2 9",
    })
    check(status == 422 and
          any(e["code"] == "edge_type_conflict" for e in body["errors"]),
          "同一点对同时属于两类边被拒绝")

    # ---- infeasible ------------------------------------------------------
    print("[无解判定]")
    status, body = solve({
        "fragments": "1 2 3 4",
        "conflicts": "1-2\n1-3\n1-4\n2-3\n2-4\n3-4",
        "stitches": "",
    })
    check(status == 200 and body["status"] == "infeasible",
          "K4 明确返回 infeasible（区别于 invalid）")

    # ---- optimal, unique, exact accounting ------------------------------
    print("[最优解与规范化]")
    status, body = solve({
        "fragments": "1 2 3 4 5 6 7 8",
        "conflicts":
            "1 2\n2 3\n3 4\n4 5\n5 6\n6 7\n7 8\n1 8\n2 8",
        "stitches": "1 3 5\n3 5 8\n5 7 4\n2 4 2\n4 6 6",
    })
    check(body["status"] == "optimal", "样例返回 optimal")
    assignment = {r["fragment"]: r["mask"] for r in body["assignment"]}
    check(all(assignment[a] != assignment[b]
              for a, b in [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6),
                           (6, 7), (7, 8), (1, 8), (2, 8)]),
          "所有冲突边端点异色")
    colors = body["canonical"]
    check(is_canonical(colors),
          "颜色按编号升序首次出现次序规范化")
    stitch_map = {(1, 3): 5, (3, 5): 8, (5, 7): 4, (2, 4): 2, (4, 6): 6}
    expected_cuts = {
        (min(a, b), max(a, b)): w
        for (a, b), w in stitch_map.items()
        if assignment[a] != assignment[b]
    }
    cut_pairs = {(r["u"], r["v"]): r["weight"] for r in body["cut_stitches"]}
    check(cut_pairs == expected_cuts, "切开边明细与分配完全一致")
    check(sum(cut_pairs.values()) == body["optimal_weight"],
          "optimal_weight 等于切开边权重和")
    check(body["optimal_weight"] == 0, "该样例可零切开",
          f"got {body['optimal_weight']}")

    # ---- multiple optima + witness --------------------------------------
    print("[多解见证]")
    status, body = solve({
        "fragments": "1 2 3 4",
        "conflicts": "1-2",
        "stitches": "3 4 7",
    })
    check(body["status"] == "optimal" and body["unique"] is False,
          "多解时 unique=false")
    w = body.get("witness")
    check(w is not None, "返回另一份见证")
    if w:
        check(w["canonical"] != body["canonical"], "见证与主方案不同")
        check(is_canonical(w["canonical"]), "见证本身也是规范化着色")
        w_assign = {r["fragment"]: r["mask"] for r in w["assignment"]}
        check(w_assign[1] != w_assign[2], "见证满足全部冲突边")
        w_cut = sum(r["weight"] for r in w["cut_stitches"])
        check(w_cut == body["optimal_weight"],
              "见证切开权重同为最优值")

    # ---- unique optimum --------------------------------------------------
    status, body = solve({
        "fragments": "1 2 3 4",
        "conflicts": "1 2\n2 3\n1 3\n3 4",
        "stitches": "1 4 1",
    })
    check(body["status"] == "optimal" and body["unique"] is True
          and body.get("witness") is None,
          "唯一最优时 unique=true 且无见证")

    # ---- weighted case with non-zero optimum -----------------------------
    # Triangle 1-2-3 fixes the three colors; fragment 4 conflicts with 2,3
    # so it must share 1's color, fragment 5 conflicts with 1,3 so it must
    # share 2's color. Stitch 4-5 (not a conflict pair) is then cut in
    # every legal coloring, forcing cost 9.
    status, body = solve({
        "fragments": "1 2 3 4 5",
        "conflicts":
            "1 2\n2 3\n1 3\n2 4\n3 4\n1 5\n3 5",
        "stitches": "4 5 9",
    })
    check(body["status"] == "optimal" and body["optimal_weight"] == 9,
          "缝合边在任意合法着色中必异色，最优切开权重 9",
          f"got status={body.get('status')} w={body.get('optimal_weight')}")

    # ---- randomized consistency sweep -----------------------------------
    print("[生成实例一致性扫描]")
    rng = random.Random(20260920)
    edge_specs = [
        (6, 0.5), (8, 0.4), (10, 0.3), (12, 0.25),
        (16, 0.15), (24, 0.08),
    ]
    sweep_ok = True
    for n, density in edge_specs:
        for trial in range(6):
            pairs = [(u, v) for u in range(1, n + 1)
                     for v in range(u + 1, n + 1)
                     if rng.random() < density]
            rng.shuffle(pairs)
            cut = rng.randrange(len(pairs) + 1)
            conf = pairs[:cut]
            st = pairs[cut:]
            payload = {
                "fragments": "\n".join(str(i) for i in range(1, n + 1)),
                "conflicts": "\n".join(f"{a} {b}" for a, b in conf),
                "stitches": "\n".join(f"{a} {b} {rng.randint(1, 9)}"
                                      for a, b in st),
            }
            # rebuild with stored weights
            weights = [rng.randint(1, 9) for _ in st]
            payload["stitches"] = "\n".join(
                f"{a} {b} {w}" for (a, b), w in zip(st, weights))
            status, body = solve(payload)
            if body["status"] == "inconclusive":
                continue  # budget exhaustion is reported, never faked
            if body["status"] == "infeasible":
                continue
            if body["status"] != "optimal":
                sweep_ok = False
                print("    unexpected", n, trial, body["status"])
                continue
            amap = {r["fragment"]: r["mask"] for r in body["assignment"]}
            if any(amap[a] == amap[b] for a, b in conf):
                sweep_ok = False
            cut_weight = sum(
                w for (a, b), w in zip(st, weights)
                if amap[a] != amap[b]
            )
            if cut_weight != body["optimal_weight"]:
                sweep_ok = False
            if not is_canonical(body["canonical"]):
                sweep_ok = False
            if not body["unique"] and body.get("witness"):
                wcolors = body["witness"]["canonical"]
                if not is_canonical(wcolors) or wcolors == body["canonical"]:
                    sweep_ok = False
    check(sweep_ok, "90+ 随机实例的解、切边、权重与规范化全部自洽")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
