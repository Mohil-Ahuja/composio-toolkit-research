"""
analyze.py — turns the three passes into the numbers on the case-study page.

Outputs:
  data/patterns.json   cluster counts across all 100 apps (auth, access, api, mcp, verdict, by category)
  data/accuracy.json   pass-1 vs pass-2 vs gold scoring on the 22-app human-verified sample,
                       plus a full-set MCP-column check against the automated endpoint probe.

Run:  python agent/analyze.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

FIELDS = ["auth", "access", "api", "mcp", "verdict"]


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def grade(pred: dict, gold: dict) -> dict[str, bool]:
    """One prediction row against one gold row. auth is list-membership, the rest are exact."""
    out = {}
    for f in FIELDS:
        if f == "auth":
            out[f] = pred["auth"] in gold["auth"]
        else:
            out[f] = pred[f] == gold[f]
    return out


def score(pred_records: list[dict], gold_records: list[dict]) -> dict:
    gold_by_id = {g["id"]: g for g in gold_records}
    per_field = defaultdict(lambda: {"hit": 0, "n": 0})
    per_app = {}
    misses = []
    for p in pred_records:
        g = gold_by_id.get(p["id"])
        if not g:
            continue
        res = grade(p, g)
        per_app[g["app"]] = sum(res.values())
        for f, ok in res.items():
            per_field[f]["n"] += 1
            per_field[f]["hit"] += int(ok)
            if not ok:
                misses.append({
                    "app": g["app"], "field": f,
                    "predicted": p[f], "verified": g[f] if f != "auth" else "/".join(g["auth"]),
                })
    hit = sum(v["hit"] for v in per_field.values())
    n = sum(v["n"] for v in per_field.values())
    return {
        "overall": {"hit": hit, "n": n, "pct": round(100 * hit / n, 1)},
        "per_field": {f: {**v, "pct": round(100 * v["hit"] / v["n"], 1)} for f, v in per_field.items()},
        "per_app": per_app,
        "misses": misses,
    }


def patterns(records: list[dict]) -> dict:
    def count(field):
        return dict(Counter(r[field] for r in records).most_common())

    by_cat = defaultdict(lambda: defaultdict(Counter))
    for r in records:
        for f in ["access", "mcp", "verdict", "auth_primary"]:
            key = r["auth"][0] if f == "auth_primary" else r[f]
            by_cat[r["category"]][f][key] += 1

    auth_any = Counter()
    for r in records:
        for a in set(r["auth"]):
            auth_any[a] += 1

    return {
        "n": len(records),
        "auth_primary": count_primary(records),
        "auth_any": dict(auth_any.most_common()),
        "access": count("access"),
        "api": count("api"),
        "mcp": count("mcp"),
        "verdict": count("verdict"),
        "confidence": count("confidence"),
        "by_category": {
            cat: {f: dict(c.most_common()) for f, c in fields.items()}
            for cat, fields in by_cat.items()
        },
        "self_serve_rate_by_category": {
            cat: round(100 * sum(
                1 for r in records
                if r["category"] == cat and r["access"].startswith("self_serve")
            ) / sum(1 for r in records if r["category"] == cat))
            for cat in {r["category"] for r in records}
        },
        "mcp_rate_by_category": {
            cat: round(100 * sum(
                1 for r in records if r["category"] == cat and r["mcp"] == "official"
            ) / sum(1 for r in records if r["category"] == cat))
            for cat in {r["category"] for r in records}
        },
    }


def count_primary(records):
    return dict(Counter(r["auth"][0] for r in records).most_common())


def mcp_column_check(final: list[dict], pass1: list[dict]) -> dict:
    """The MCP column is the one field verified across the WHOLE set by the automated probe,
    so it gives a full-population accuracy read on the parametric baseline, not just a sample."""
    p1 = {r["id"]: r["mcp"] for r in pass1}
    hit = sum(1 for r in final if p1.get(r["id"]) == r["mcp"])
    flips = [
        {"app": r["app"], "baseline": p1[r["id"]], "verified": r["mcp"]}
        for r in final if p1.get(r["id"]) != r["mcp"]
    ]
    return {
        "n": len(final),
        "baseline_correct": hit,
        "baseline_pct": round(100 * hit / len(final), 1),
        "flips": flips,
        "flip_direction": dict(Counter(f"{f['baseline']}->{f['verified']}" for f in flips).most_common()),
    }


def main() -> None:
    findings = load("findings.json")["records"]
    pass1 = load("pass1_baseline.json")["records"]
    pass2 = load("pass2_sample.json")["records"]
    gold = load("gold_sample.json")["records"]

    gold_ids = {g["id"] for g in gold}
    pass1_sample = [r for r in pass1 if r["id"] in gold_ids]

    accuracy = {
        "sample_size": len(gold),
        "judgments_per_pass": len(gold) * len(FIELDS),
        "pass1_parametric": score(pass1_sample, gold),
        "pass2_single_doc": score(pass2, gold),
        "mcp_full_set_check": mcp_column_check(findings, pass1),
    }
    accuracy["delta_overall_pct"] = round(
        accuracy["pass2_single_doc"]["overall"]["pct"] - accuracy["pass1_parametric"]["overall"]["pct"], 1
    )

    (DATA / "patterns.json").write_text(json.dumps(patterns(findings), indent=2), encoding="utf-8")
    (DATA / "accuracy.json").write_text(json.dumps(accuracy, indent=2), encoding="utf-8")

    p = patterns(findings)
    print(f"apps: {p['n']}")
    print("auth (primary):", p["auth_primary"])
    print("access:", p["access"])
    print("mcp:", p["mcp"])
    print("verdict:", p["verdict"])
    print()
    print("pass1 parametric :", accuracy["pass1_parametric"]["overall"])
    print("pass2 single-doc :", accuracy["pass2_single_doc"]["overall"])
    print("mcp full-set     :", {k: v for k, v in accuracy["mcp_full_set_check"].items() if k != "flips"})


if __name__ == "__main__":
    main()
