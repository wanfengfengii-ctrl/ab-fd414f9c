"""Cross-check the exact solver against exhaustive enumeration."""

import itertools
import random
import sys

sys.path.insert(0, ".")
from app.solver import solve  # noqa: E402


def brute(n, conflict, stitch):
    adj = [set() for _ in range(n)]
    for u, v in conflict:
        adj[u].add(v)
        adj[v].add(u)
    best = None
    canon_set = set()
    for raw in itertools.product(range(3), repeat=n):
        if any(raw[u] == raw[v] for u, v in conflict):
            continue
        cost = sum(w for (u, v), w in stitch.items() if raw[u] != raw[v])
        mapping = {}
        nxt = 0
        canon = []
        for c in raw:
            if c not in mapping:
                mapping[c] = nxt
                nxt += 1
            canon.append(mapping[c])
        canon = tuple(canon)
        if best is None or cost < best:
            best = cost
            canon_set = {canon}
        elif cost == best:
            canon_set.add(canon)
    if best is None:
        return None
    return best, sorted(canon_set)


def random_instance(rng, n):
    # Conflict and stitch edges are chosen independently from disjoint
    # pairs, so stitch edges may join vertices in different conflict
    # components (the union graph may differ from the conflict graph).
    conflict, stitch = [], {}
    for u in range(n):
        for v in range(u + 1, n):
            r = rng.random()
            if r < 0.3:
                conflict.append((u, v))
            elif r < 0.55:
                stitch[(u, v)] = rng.randint(1, 9)
    rng.shuffle(conflict)
    return conflict, stitch


def main():
    rng = random.Random(20260920)
    trials = 0
    for n in range(1, 9):
        for _ in range(150):
            conflict, stitch = random_instance(rng, n)
            res = solve(n, conflict, stitch)
            ref = brute(n, conflict, stitch)
            trials += 1
            if ref is None:
                assert not res.feasible, (n, conflict, stitch, res)
                continue
            best, canon_set = ref
            assert res.feasible, (n, conflict, stitch)
            assert res.optimal_weight == best, (
                n, conflict, stitch, res.optimal_weight, best
            )
            assert res.canonical == canon_set[0], (
                n, conflict, stitch, res.canonical, canon_set
            )
            expected_unique = len(canon_set) == 1
            assert res.unique == expected_unique, (
                n, conflict, stitch, res, len(canon_set)
            )
            if not expected_unique:
                assert res.witness is not None
                assert res.witness in canon_set
                assert res.witness != res.canonical
    print(f"all {trials} randomized trials passed")

    # handcrafted: K4 -> infeasible
    k4 = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    assert not solve(4, k4, {}).feasible

    # unique coloring: triangle plus pendant constraints
    tri = [(0, 1), (1, 2), (0, 2)]
    r = solve(4, tri + [(2, 3)], {(0, 3): 1})
    assert r.feasible and r.optimal_weight == 0 and r.unique, r
    assert r.canonical == (0, 1, 2, 0), r.canonical

    # multiple optima: two disconnected edges -> many canonical colorings
    r = solve(4, [(0, 1), (2, 3)], {})
    assert r.feasible and r.optimal_weight == 0
    assert not r.unique and r.witness != r.canonical
    assert r.canonical == (0, 1, 0, 1), r.canonical
    # witnesses are actual optimal canonical colorings
    assert all(
        r.canonical[i] != r.canonical[j] for i, j in [(0, 1), (2, 3)]
    )
    assert all(
        r.witness[i] != r.witness[j] for i, j in [(0, 1), (2, 3)]
    )

    # stitch actually forces cost: path 0-1-2 with weight on endpoints
    r = solve(3, [(0, 1), (1, 2)], {(0, 2): 5})
    assert r.feasible and r.optimal_weight == 0  # can color endpoints same
    r = solve(3, [(0, 1), (1, 2), (0, 2)], {(0, 2): 5})
    assert r.feasible and r.optimal_weight == 5

    # 48-vertex sparse graph completes quickly
    big_conf = [(i, i + 1) for i in range(47)]
    r = solve(48, big_conf, {(0, 47): 7})
    assert r.feasible and r.optimal_weight == 0
    print("handcrafted cases passed")


if __name__ == "__main__":
    main()
