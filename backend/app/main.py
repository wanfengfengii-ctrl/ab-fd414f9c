"""FastAPI workbench: validate a three-mask coloring instance, solve it
exactly, and return the canonical optimum together with the cut stitches.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .solver import solve
from .validation import (
    MAX_FRAGMENTS,
    MIN_FRAGMENTS,
    ValidationError,
    parse_input,
)

MASK_NAMES = ("mask1", "mask2", "mask3")
MASK_LABELS = ("掩模一", "掩模二", "掩模三")


class SolveRequest(BaseModel):
    fragments: str = Field(default="", description="片段编号，每行一个")
    conflicts: str = Field(default="", description="冲突边，每行两个编号")
    stitches: str = Field(default="", description="缝合边，每行 端点1 端点2 权重")


app = FastAPI(title="三掩模分配工作台", version="1.0.0")

# In container deployments the browser dev server / static host differs.
_cors = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
def _bad_request(_req: Request, _exc: RequestValidationError) -> JSONResponse:
    # Malformed JSON / wrong field types: keep the same itemized-error
    # envelope so the client only ever handles one invalid shape.
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "status": "invalid",
            "errors": [{
                "code": "malformed_request",
                "message": "请求体格式不正确，需要包含 fragments/conflicts/stitches 三个文本字段",
                "field": "fragments",
            }],
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/info")
def info() -> dict:
    return {
        "min_fragments": MIN_FRAGMENTS,
        "max_fragments": MAX_FRAGMENTS,
        "masks": [
            {"id": MASK_NAMES[i], "label": MASK_LABELS[i]} for i in range(3)
        ],
    }


def _assignment_rows(ids: list[int], coloring: tuple[int, ...]) -> list[dict]:
    return [
        {"fragment": ids[i], "mask": MASK_NAMES[coloring[i]]}
        for i in range(len(ids))
    ]


def _cut_stitches(
    stitches: list[tuple[int, int, int]],
    index: dict[int, int],
    coloring: tuple[int, ...],
) -> list[dict]:
    rows = []
    for a, b, w in stitches:
        ca, cb = coloring[index[a]], coloring[index[b]]
        if ca != cb:
            rows.append(
                {
                    "u": a,
                    "v": b,
                    "weight": w,
                    "mask_u": MASK_NAMES[ca],
                    "mask_v": MASK_NAMES[cb],
                }
            )
    rows.sort(key=lambda r: (-r["weight"], r["u"], r["v"]))
    return rows


@app.post("/api/solve")
def solve_endpoint(req: SolveRequest) -> JSONResponse:
    try:
        parsed = parse_input(req.fragments, req.conflicts, req.stitches)
    except ValidationError as exc:
        # Input errors must be shown item by item and clear stale results.
        return JSONResponse(
            status_code=422,
            content={"ok": False, "status": "invalid", "errors": exc.errors},
        )

    ids = parsed.fragment_ids
    index = {fid: i for i, fid in enumerate(ids)}
    conflict_idx = [(index[a], index[b]) for a, b in parsed.conflicts]
    stitch_idx = {
        (index[a], index[b]): w for a, b, w in parsed.stitches
    }

    started = time.perf_counter()
    result = solve(len(ids), conflict_idx, stitch_idx,
                   time_limit=float(os.getenv("SOLVER_TIME_LIMIT", "20")))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    if result.inconclusive:
        # Budget exhausted: distinct from both invalid input and a proof of
        # infeasibility.
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "status": "inconclusive",
                "message": "已在时间预算内未完成精确搜索，无法确认最优性或无解",
                "stats": {"nodes": result.nodes, "elapsed_ms": elapsed_ms},
            },
        )

    if not result.feasible:
        # No proper 3-coloring of the conflict graph: distinguish clearly
        # from invalid input and from an optimal solution.
        return JSONResponse(
            content={
                "ok": True,
                "status": "infeasible",
                "message": "冲突图不存在合法三色着色，无法分配到三张掩模",
                "stats": {"nodes": result.nodes, "elapsed_ms": elapsed_ms},
            }
        )

    assert result.canonical is not None and result.optimal_weight is not None
    payload: dict = {
        "ok": True,
        "status": "optimal",
        "unique": result.unique,
        "optimal_weight": result.optimal_weight,
        "total_stitch_weight": sum(w for _, _, w in parsed.stitches),
        "assignment": _assignment_rows(ids, result.canonical),
        "cut_stitches": _cut_stitches(
            parsed.stitches, index, result.canonical
        ),
        "canonical": list(result.canonical),
        "stats": {"nodes": result.nodes, "elapsed_ms": elapsed_ms},
    }
    if result.witness is not None:
        payload["witness"] = {
            "assignment": _assignment_rows(ids, result.witness),
            "cut_stitches": _cut_stitches(
                parsed.stitches, index, result.witness
            ),
            "canonical": list(result.witness),
        }
    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# Static frontend (production image serves the Vite build from FastAPI)
# ---------------------------------------------------------------------------

_STATIC_DIR = os.getenv("STATIC_DIR", "/app/static")

if os.path.isdir(_STATIC_DIR):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")),
        name="assets",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    @app.get("/favicon.svg")
    def favicon() -> FileResponse:
        return FileResponse(
            os.path.join(_STATIC_DIR, "favicon.svg"),
            media_type="image/svg+xml",
        )

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        # Unknown non-API GET routes return the SPA shell.
        if full_path.startswith(("api/", "health")):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
