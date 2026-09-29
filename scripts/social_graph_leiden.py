#!/usr/bin/env python3
"""social_graph_leiden.py — community detection + broker ranking for a weighted social graph.

Deep-dive companion for kid-social-watch (AD HOC investigations only — never run
graph builds in a daily cron; see the child's watch skill). Given a weighted
interaction graph — nodes are people, edge weights are observed interaction
strength (the proven recipe: DM volume x3, log-scaled, + shared-group co-activity
log1p(min(count_A, count_B)) per group; method reference: the
social-graph-community-detection skill) — this runner does:

  1. Leiden with a resolution sweep (0.5 .. 1.5) — communities at several granularities
  2. Louvain cross-check (igraph community_multilevel, same weights)
  3. Betweenness centrality on strength-derived distances — the brokers between circles
  4. Robustness verdict — pairs that stay together across EVERY resolution AND Louvain

Input JSON (file path or "-" for stdin):
  {
    "target": "Alex",                      # optional — report the target's community
    "nodes": ["Alex", "Jo"],               # optional — inferred from edges otherwise
    "edges": [["Alex", "Jo", 12.5]]        # [a, b, weight] — weight > 0; duplicates aggregate
  }

Deps:  pip install leidenalg igraph   (leidenalg optional — falls back to Louvain only)
Usage: python3 social_graph_leiden.py graph.json [--target X] [--top 10] [--json]
"""
import argparse
import json
import sys

try:
    import igraph as ig
except ImportError:
    sys.exit("missing dependency: pip install leidenalg igraph")

try:
    import leidenalg as la
except ImportError:
    la = None

RESOLUTIONS = [0.5, 0.8, 1.0, 1.2, 1.5]


def load_spec(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


def build_graph(spec):
    agg = {}
    for a, b, w in spec.get("edges", []):
        w = float(w)
        if w <= 0 or a == b:
            continue
        key = (a, b) if str(a) <= str(b) else (b, a)
        agg[key] = agg.get(key, 0.0) + w
    edges = [(a, b, w) for (a, b), w in agg.items()]

    nodes = []
    for n in spec.get("nodes") or []:
        if n not in nodes:
            nodes.append(n)
    for a, b, _w in edges:
        for n in (a, b):
            if n not in nodes:
                nodes.append(n)

    idx = {n: i for i, n in enumerate(nodes)}
    g = ig.Graph(n=len(nodes))
    g.vs["name"] = nodes
    if edges:
        g.add_edges([(idx[a], idx[b]) for a, b, _ in edges])
        g.es["weight"] = [w for _, _, w in edges]
    return g, nodes


def leiden_sweep(g):
    out = []
    if la is None:
        return out
    kwargs = {"weights": "weight"} if g.ecount() else {}
    for res in RESOLUTIONS:
        part = la.find_partition(g, la.RBConfigurationVertexPartition,
                                 resolution_parameter=res, seed=42, **kwargs)
        # leidenalg note: part.modularity is a FLOAT ATTRIBUTE, not a method.
        out.append({"resolution": res, "modularity": float(part.modularity),
                    "membership": list(part.membership)})
    return out


def louvain(g):
    kwargs = {"weights": "weight"} if g.ecount() else {}
    return list(g.community_multilevel(**kwargs).membership)


def pairs_of(membership):
    groups = {}
    for i, c in enumerate(membership):
        groups.setdefault(c, []).append(i)
    s = set()
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                s.add((min(members[i], members[j]), max(members[i], members[j])))
    return s


def main():
    ap = argparse.ArgumentParser(
        description="Leiden/Louvain communities + brokers for a weighted social graph")
    ap.add_argument("spec", help="JSON spec file with nodes/edges (or '-' for stdin)")
    ap.add_argument("--target", default=None, help="node to drill into (overrides spec.target)")
    ap.add_argument("--top", type=int, default=10, help="top-N brokers to list")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    spec = load_spec(args.spec)
    g, nodes = build_graph(spec)
    target = args.target or spec.get("target")

    sweep = leiden_sweep(g)
    louv = louvain(g)

    # betweenness: strength -> distance (a strong tie is a SHORT path)
    if g.ecount():
        dists = [1.0 / max(w, 1e-9) for w in g.es["weight"]]
        bet = g.betweenness(weights=dists)
    else:
        bet = [0.0] * len(nodes)
    brokers = sorted(zip(nodes, bet), key=lambda kv: -kv[1])

    # robustness: pairs together across EVERY resolution AND Louvain
    robust = None
    for row in sweep:
        p = pairs_of(row["membership"])
        robust = p if robust is None else (robust & p)
    if robust is not None:
        robust &= pairs_of(louv)
    else:
        robust = set()

    report = {
        "nodes": len(nodes), "edges": g.ecount(),
        "leiden": sweep, "louvain_communities": len(set(louv)), "louvain_membership": louv,
        "brokers": [{"name": n, "betweenness": round(b, 3)} for n, b in brokers[: args.top]],
        "robust_pairs": len(robust),
    }
    if target and target in nodes:
        ti = nodes.index(target)
        report["target"] = target
        report["target_communities"] = [
            {"resolution": r["resolution"], "community": r["membership"][ti]} for r in sweep]
        report["target_stable_group"] = sorted(
            {nodes[j] for i, j in robust if ti in (i, j)})

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    print("graph: %d nodes, %d edges" % (report["nodes"], report["edges"]))
    if sweep:
        print("\nLeiden resolution sweep:")
        for row in sweep:
            print("  res %.1f | modularity %.4f | %d communities"
                  % (row["resolution"], row["modularity"], len(set(row["membership"]))))
    else:
        print("\nLeiden: NOT installed (pip install leidenalg) — Louvain-only results below")
    print("\nLouvain cross-check: %d communities" % report["louvain_communities"])
    print("\nTop brokers (betweenness, strength-as-proximity):")
    for b in report["brokers"]:
        print("  %-28s %.3f" % (b["name"], b["betweenness"]))
    if target and "target_communities" in report:
        print("\nTarget %r community per resolution: %s" % (
            target, ", ".join("%.1f->%d" % (r["resolution"], r["community"])
                              for r in report["target_communities"])))
        if report["target_stable_group"]:
            print("Stable group with %s (every resolution + Louvain):" % target)
            for n in report["target_stable_group"]:
                print("  - %s" % n)
        else:
            print("Stable group with %s: none survives the full sweep" % target)
    print("\nRobust pairs (same community across ALL resolutions + Louvain): %d"
          % report["robust_pairs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
