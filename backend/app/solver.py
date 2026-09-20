"""Exact three-mask coloring with minimum cut-stitch weight.

The mask assignment problem:

* every fragment is assigned one of three masks (0/1/2);
* the endpoints of every conflict edge must be on different masks (the
  conflict graph must admit a proper 3-coloring);
* a stitch edge whose endpoints land on different masks is "cut" and its
  positive integer weight is paid (overlay / stitching risk);
* the total cut weight is minimized exactly.

Solver architecture
-------------------

The union graph (conflict + stitch edges) is split into connected
components, which are independent for both feasibility and cost.  Each
component is solved exactly by bucket-elimination variable elimination
(VE) along a min-fill ordering: it runs in O(n * 3**(treewidth+1)), which
is instant for the sparse / structured graphs typical of layout data, and
simultaneously proves feasibility and the minimum cut weight.  Components
whose induced width exceeds a budget fall back to a DSATUR branch-and-bound
search with forward checking (dense graphs have few colorings and are
quick there, whether feasible or not).

Phase two reconstructs the lexicographically smallest *canonical* optimum:
colors are normalized by first appearance in ascending fragment id
(vertex 0 fixed at 0, color 2 allowed only after color 1 has appeared).
The canonical coloring is built position by position; a color is viable
iff an optimal completion exists.  Repeating the build while requiring a
strictly greater result yields a second optimum witness, or proves the
canonical optimum unique.

A wall-clock / node budget is reported separately as "inconclusive"; it is
never confused with a proof of infeasibility.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

INF = 10**30
_COLORS = (0, 1, 2)
# Total table-entry visits allowed across VE buckets before falling back.
VE_WORK_BUDGET = 6_000_000


@dataclass(frozen=True)
class SolveResult:
    feasible: bool
    optimal_weight: int | None
    canonical: tuple[int, ...] | None
    witness: tuple[int, ...] | None
    unique: bool
    nodes: int
    timed_out: bool = False
    inconclusive: bool = False


# ---------------------------------------------------------------------------
# Connected components of the union graph
# ---------------------------------------------------------------------------


def _components(
    n: int,
    conflict: list[tuple[int, int]],
    stitch: dict[tuple[int, int], int],
) -> list[list[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for u, v in conflict:
        union(u, v)
    for u, v in stitch:
        union(u, v)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Bucket-elimination variable elimination
# ---------------------------------------------------------------------------


def _min_fill_order(
    free: list[int],
    adj_map: dict[int, set[int]],
    cap: int,
) -> tuple[list[int], list[int]] | None:
    """Greedy min-fill elimination order.

    Returns (order, bucket_widths), where bucket_widths[k] is the number of
    neighbors surviving vertex order[k] in the induced graph (the resulting
    message has that many variables).  None if induced width exceeds cap.
    """
    remaining = set(free)
    order: list[int] = []
    widths: list[int] = []
    radj = {v: set(adj_map[v]) for v in free}
    while remaining:
        best_v = -1
        best_fill = None
        best_deg = -1
        for v in remaining:
            nbrs = radj[v]
            deg = len(nbrs)
            nlist = list(nbrs)
            fill = 0
            for i in range(len(nlist)):
                for j in range(i + 1, len(nlist)):
                    if nlist[j] not in radj[nlist[i]]:
                        fill += 1
            if best_fill is None or fill < best_fill or (
                fill == best_fill and deg < best_deg
            ):
                best_fill, best_deg, best_v = fill, deg, v
        if best_deg - 1 > cap:
            return None
        order.append(best_v)
        widths.append(best_deg - 1)
        nlist = list(radj[best_v])
        for i in range(len(nlist)):
            for j in range(i + 1, len(nlist)):
                a, b = nlist[i], nlist[j]
                radj[a].add(b)
                radj[b].add(a)
        for a in nlist:
            radj[a].discard(best_v)
        remaining.discard(best_v)
    return order, widths


def ve_min(
    verts: list[int],
    conflict: list[tuple[int, int]],
    stitch: dict[tuple[int, int], int],
    fixed: dict[int, int] | None,
    deadline: float,
    width_cap: int = 11,
) -> tuple[bool, int | None, list[int] | None]:
    """Exact minimum stitch cost over one residual component.

    `verts` are the uncolored vertices; `fixed` maps already colored
    boundary vertices (incident to the component) to colors.
    Returns (feasible, min_cost, order); order is None when the induced
    width exceeds the cap (caller falls back to branch and bound).
    """
    fixed = fixed or {}
    vset = set(verts)
    free = [v for v in verts if v not in fixed]

    # Incidence restricted to this component's uncolored vertices.
    union_adj: dict[int, set[int]] = {v: set() for v in free}
    conf_peers: dict[int, set[int]] = {v: set() for v in free}
    sw_peers: dict[int, dict[int, int]] = {v: {} for v in free}

    for u, v in conflict:
        if u in vset and v in vset and u not in fixed and v not in fixed:
            union_adj[u].add(v)
            union_adj[v].add(u)
            conf_peers[u].add(v)
            conf_peers[v].add(u)
    for (u, v), w in stitch.items():
        if u in vset and v in vset and u not in fixed and v not in fixed:
            union_adj[u].add(v)
            union_adj[v].add(u)
            sw_peers[u][v] = w
            sw_peers[v][u] = w

    # Unary tables for free vars: edges toward fixed boundary vertices.
    unary: dict[int, list] = {}
    for v in free:
        costs = [0, 0, 0]
        allowed = [True, True, True]
        for u, x in conflict:
            peer = -1
            if u == v and x in fixed:
                peer = x
            elif x == v and u in fixed:
                peer = u
            if peer != -1:
                allowed[fixed[peer]] = False
        for (u, x), w in stitch.items():
            peer = -1
            if u == v and x in fixed:
                peer = x
            elif x == v and u in fixed:
                peer = u
            if peer != -1:
                fc = fixed[peer]
                for c in _COLORS:
                    if c != fc:
                        costs[c] += w
        unary[v] = [costs[c] if allowed[c] else INF for c in _COLORS]
        if all(x >= INF for x in unary[v]):
            return False, None, []

    if not free:
        return True, 0, []

    ordered = _min_fill_order(free, union_adj, width_cap)
    if ordered is None:
        return True, None, None
    order, widths = ordered
    opos = {v: i for i, v in enumerate(order)}

    # Estimate VE cost by simulating the factor/message flow.  Every bucket
    # starts with its vertex unary plus one binary factor per original edge
    # charged to the endpoint earlier in the order; eliminating a vertex
    # creates one message placed in the earliest surviving neighbor's
    # bucket.  Cost per bucket = 3 colors * 3**(scope size) * factor count.
    bucket_factors = {v: 1 for v in free}
    for u in free:
        for x in union_adj[u]:
            if opos[x] > opos[u]:
                bucket_factors[u] += 1
    radj_sim = {v: set(union_adj[v]) for v in free}
    ve_work = 0
    for v in order:
        nlist = list(radj_sim[v])
        scope_size = len(nlist)
        ve_work += 3 * (3 ** scope_size) * bucket_factors[v]
        if scope_size:
            target = min(nlist, key=lambda x: opos[x])
            bucket_factors[target] += 1
            for i in range(len(nlist)):
                for j in range(i + 1, len(nlist)):
                    radj_sim[nlist[i]].add(nlist[j])
                    radj_sim[nlist[j]].add(nlist[i])
            for x in nlist:
                radj_sim[x].discard(v)
    if ve_work > VE_WORK_BUDGET:
        return True, None, None

    # A factor is (scope in elimination order, table list of length
    # 3**len(scope)); code digit k (base 3) is the color of scope[k].
    # Each bucket additionally records the stride of the bucket variable's
    # digit in each factor (0 when the factor does not contain it).
    buckets: dict[int, list[tuple[tuple, list, int]]] = {v: [] for v in free}
    constants = 0

    def place(scope: tuple[int, ...], table: list, stride: int) -> None:
        nonlocal constants
        if scope:
            earliest = min(scope, key=lambda x: opos[x])
            buckets[earliest].append((scope, table, stride))
        else:
            constants += table[0]

    for v in free:
        place((v,), [unary[v][c] for c in _COLORS], 1)
    for u in vset:
        if u in fixed:
            continue
        for v in conf_peers[u]:
            if v <= u:
                continue
            a, b = (u, v) if opos[u] < opos[v] else (v, u)
            # scope (a, b): digit0 = color(a), digit1 = color(b); the factor
            # lands in a's bucket, and a is digit 0 (stride 1).
            table = [
                0 if (code % 3) != (code // 3) else INF
                for code in range(9)
            ]
            place((a, b), table, 1)
        for v, w in sw_peers[u].items():
            if v <= u:
                continue
            a, b = (u, v) if opos[u] < opos[v] else (v, u)
            table = [
                0 if (code % 3) == (code // 3) else w
                for code in range(9)
            ]
            place((a, b), table, 1)

    def eliminate(v: int) -> bool:
        factors = buckets[v]
        scope = tuple(sorted(
            {x for sc, _, _ in factors for x in sc if x != v},
            key=lambda x: opos[x],
        ))
        m = len(scope)
        size = 3 ** m
        # Per factor: base factor-code for each output row (v digit omitted)
        # and the multiplier for v's digit inside the factor code.
        plans = []
        for fscope, _table, _stride in factors:
            pos_in_factor = {x: j for j, x in enumerate(fscope)}
            vmult = 3 ** pos_in_factor[v]
            coeffs = [0] * m
            for k, x in enumerate(scope):
                if x in pos_in_factor:
                    coeffs[k] = 3 ** pos_in_factor[x]
            base = [0] * size
            for code in range(size):
                tmp = code
                acc = 0
                for k in range(m):
                    acc += (tmp % 3) * coeffs[k]
                    tmp //= 3
                base[code] = acc
            plans.append((base, vmult))
        best = [INF] * size
        any_row = False
        for code in range(size):
            row_best = INF
            for c in _COLORS:
                total = 0
                ok = True
                for fi, (fscope, table, _vs) in enumerate(factors):
                    base, vmult = plans[fi]
                    val = table[base[code] + c * vmult]
                    if val >= INF:
                        ok = False
                        break
                    total += val
                if ok and total < row_best:
                    row_best = total
            if row_best < INF:
                best[code] = row_best
                any_row = True
            if (code & 8191) == 8191 and time.perf_counter() > deadline:
                return False
        if not any_row:
            return False  # infeasible at this bucket
        place(scope, best, 1)
        return True

    for v in order:
        if not buckets[v]:
            continue
        if not eliminate(v):
            if time.perf_counter() > deadline:
                return True, None, None
            return False, None, order

    return True, constants, order


# ---------------------------------------------------------------------------
# DSATUR branch-and-bound fallback
# ---------------------------------------------------------------------------


def bb_min(
    verts: list[int],
    conflict: list[tuple[int, int]],
    stitch: dict[tuple[int, int], int],
    fixed: dict[int, int] | None,
    deadline: float,
    node_limit: int = 4_000_000,
) -> tuple[bool, int | None, bool, int]:
    """Minimum stitch cost for one component.

    With no fixed boundary the first chosen vertex is pinned to color 0
    (mask labels are interchangeable, so the optimum weight is unchanged).
    Returns (feasible, min_cost, timed_out, nodes).
    """
    fixed = dict(fixed or {})
    n = len(verts)
    pos = {v: i for i, v in enumerate(verts)}
    vset = set(verts)
    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v in conflict:
        if u in vset and v in vset:
            adj[pos[u]].append(v)
            adj[pos[v]].append(u)
    wmap: list[dict[int, int]] = [dict() for _ in range(n)]
    for (u, v), w in stitch.items():
        if u in vset and v in vset:
            wmap[pos[u]][v] = w
            wmap[pos[v]][u] = w

    color = [-1] * n
    degree = [len(adj[i]) for i in range(n)]
    for v, c in fixed.items():
        if v in pos:
            color[pos[v]] = c

    # Contributions from / constraints against fixed boundary vertices.
    boundary_cost = [[0, 0, 0] for _ in range(n)]
    boundary_forbidden = [0 for _ in range(n)]
    for u, v in conflict:
        if u in vset and v in fixed:
            boundary_forbidden[pos[u]] |= 1 << fixed[v]
        if v in vset and u in fixed:
            boundary_forbidden[pos[v]] |= 1 << fixed[u]
    for (u, v), w in stitch.items():
        if u in vset and v in fixed:
            fc = fixed[v]
            for c in _COLORS:
                if c != fc:
                    boundary_cost[pos[u]][c] += w
        if v in vset and u in fixed:
            fc = fixed[u]
            for c in _COLORS:
                if c != fc:
                    boundary_cost[pos[v]][c] += w

    nodes = 0
    timed_out = False
    best: int | None = None

    def tick() -> bool:
        nonlocal nodes, timed_out
        nodes += 1
        if nodes > node_limit or time.perf_counter() > deadline:
            timed_out = True
            return False
        return True

    def added_cost(i: int, c: int) -> int:
        total = boundary_cost[i][c]
        for v, w in wmap[i].items():
            cv = color[pos[v]]
            if cv != -1 and cv != c:
                total += w
        return total

    def lower_bound() -> int:
        total = 0
        for i in range(n):
            if color[i] != -1:
                continue
            costs = boundary_cost[i][:]
            forced = any(costs)
            for v, w in wmap[i].items():
                cv = color[pos[v]]
                if cv != -1:
                    forced = True
                    for c in _COLORS:
                        if c != cv:
                            costs[c] += w
            if forced:
                total += min(costs)
        return total

    def forward_check(i: int, c: int) -> bool:
        color[i] = c
        ok = True
        for v in adj[i]:
            j = pos[v]
            if color[j] != -1:
                continue
            used = 0
            for nb in adj[j]:
                cn = color[pos[nb]]
                if cn != -1:
                    used |= 1 << cn
            if used == 0b111:
                ok = False
                break
        color[i] = -1
        return ok

    def pick() -> int:
        bi, best_sat, best_deg = -1, -1, -1
        for i in range(n):
            if color[i] != -1:
                continue
            seen = 0
            sat = 0
            for v in adj[i]:
                cn = color[pos[v]]
                if cn != -1:
                    bit = 1 << cn
                    if not (seen & bit):
                        seen |= bit
                        sat += 1
            if sat > best_sat or (sat == best_sat and degree[i] > best_deg):
                best_sat, best_deg, bi = sat, degree[i], i
        return bi

    def branch(depth: int, cost: int, pin_open: bool) -> None:
        nonlocal best
        if not tick():
            return
        if depth == n:
            if best is None or cost < best:
                best = cost
            return
        if best is not None:
            if cost >= best:
                return
            if cost + lower_bound() >= best:
                return

        i = pick()
        used = boundary_forbidden[i]
        for v in adj[i]:
            cn = color[pos[v]]
            if cn != -1:
                used |= 1 << cn

        if pin_open:
            candidates = [(0, 0)] if not (used & 1) else []
        else:
            candidates = sorted(
                (added_cost(i, c), c)
                for c in _COLORS
                if not (used & (1 << c))
            )

        for extra, c in candidates:
            new_cost = cost + extra
            if best is not None and new_cost >= best:
                continue
            if not forward_check(i, c):
                continue
            color[i] = c
            branch(depth + 1, new_cost, False)
            color[i] = -1
            if timed_out:
                return

    initial_depth = sum(1 for x in color if x != -1)
    branch(initial_depth, 0, not fixed)

    if timed_out:
        return True, None, True, nodes
    if best is None:
        return False, None, False, nodes
    return True, best, False, nodes


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _component_min(
    verts: list[int],
    conflict: list[tuple[int, int]],
    stitch: dict[tuple[int, int], int],
    fixed: dict[int, int] | None,
    deadline: float,
) -> tuple[bool, int | None, bool, int]:
    feasible, cost, order = ve_min(
        verts, conflict, stitch, fixed, deadline
    )
    if order is not None:
        return feasible, cost, False, 0
    return bb_min(verts, conflict, stitch, fixed, deadline)


def solve(
    n: int,
    conflict: list[tuple[int, int]],
    stitch: dict[tuple[int, int], int],
    time_limit: float = 20.0,
) -> SolveResult:
    """Exact optimum plus uniqueness of its canonical coloring.

    `n` fragments numbered 0..n-1; `conflict` undirected edges; `stitch`
    maps undirected (lo, hi) pairs to positive weights.
    """
    started = time.perf_counter()
    deadline = started + time_limit
    if n == 0:
        return SolveResult(True, 0, (), None, True, 0)

    comps = _components(n, conflict, stitch)

    nodes_total = 0
    total_weight = 0
    for comp in comps:
        feasible, cost, inconclusive, nodes = _component_min(
            comp, conflict, stitch, None, deadline
        )
        nodes_total += nodes
        if not feasible:
            return SolveResult(False, None, None, None, False, nodes_total)
        if inconclusive or cost is None:
            return SolveResult(False, None, None, None, False,
                               nodes_total, True, True)
        total_weight += cost

    # ---- phase B: canonical lex optimum and witness ----------------------

    union_adj: list[set[int]] = [set() for _ in range(n)]
    conflict_adj: list[set[int]] = [set() for _ in range(n)]
    for u, v in conflict:
        union_adj[u].add(v)
        union_adj[v].add(u)
        conflict_adj[u].add(v)
        conflict_adj[v].add(u)
    for u, v in stitch:
        union_adj[u].add(v)
        union_adj[v].add(u)

    def completion_min(color: list[int]) -> int | None:
        """Min stitch cost to finish the partial coloring.

        Uncolored vertices are split into components of the union graph
        after the colored prefix is removed; colored neighbors act as a
        fixed boundary.  Component minima add up.  None means infeasible;
        -1 means the time budget was exhausted.
        """
        remaining = {i for i in range(n) if color[i] == -1}
        total = 0
        while remaining:
            seed = next(iter(remaining))
            queue = [seed]
            remaining.discard(seed)
            comp: list[int] = []
            boundary: dict[int, int] = {}
            while queue:
                u = queue.pop()
                comp.append(u)
                for v in union_adj[u]:
                    if color[v] != -1:
                        boundary[v] = color[v]
                    elif v in remaining:
                        remaining.discard(v)
                        queue.append(v)
            feasible, cost, inconclusive, _ = _component_min(
                comp, conflict, stitch, boundary, deadline
            )
            if not feasible:
                return None
            if inconclusive or cost is None:
                return -1
            total += cost
        return total

    color = [-1] * n
    color[0] = 0

    def build(
        i: int,
        cost: int,
        diverged: bool,
        greater_than: tuple[int, ...] | None,
    ) -> tuple[int, ...] | None:
        if i == n:
            if cost != total_weight:
                return None
            if greater_than is not None and not diverged:
                return None
            return tuple(color)
        if time.perf_counter() > deadline:
            return None
        for c in _COLORS:
            # Properness is only over conflict edges.
            if any(color[nb] == c for nb in conflict_adj[i]):
                continue
            # Canonicality: color 2 cannot appear before color 1 does.
            if c == 2 and 1 not in color[:i]:
                continue
            equal = False
            if greater_than is not None and not diverged:
                if c < greater_than[i]:
                    continue
                equal = c == greater_than[i]
            extra = 0
            for (a, b), w in stitch.items():
                if a == i and color[b] != -1 and color[b] != c:
                    extra += w
                elif b == i and color[a] != -1 and color[a] != c:
                    extra += w
            if cost + extra > total_weight:
                continue
            color[i] = c
            keep: tuple[int, ...] | None = None
            if equal:
                # Follow the reference prefix; strict divergence is enforced
                # recursively at a later position (with backtracking).
                keep = build(i + 1, cost + extra, False, greater_than)
            else:
                residual = total_weight - cost - extra
                m = completion_min(color)
                if m == -1:
                    color[i] = -1
                    return None
                if m is not None and m <= residual:
                    keep = build(i + 1, cost + extra, True, greater_than)
            color[i] = -1
            if keep is not None:
                return keep
        return None

    first = build(1, 0, True, None)
    if first is None:
        return SolveResult(False, None, None, None, False,
                           nodes_total, True, True)
    second = build(1, 0, False, first)
    if second is None:
        if time.perf_counter() > deadline:
            return SolveResult(False, None, None, None, False,
                               nodes_total, True, True)
    return SolveResult(
        feasible=True,
        optimal_weight=total_weight,
        canonical=first,
        witness=second,
        unique=second is None,
        nodes=nodes_total,
    )
