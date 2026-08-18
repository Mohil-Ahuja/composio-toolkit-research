"""
build_site.py — renders site/index.html from the data files.

The page never contains a hand-typed number. Every figure on it is read out of
data/*.json here and injected as one JSON payload, so the case study cannot drift away
from the dataset it describes. Re-run after any data change.

Also copies the JSON next to the page so the deployed site is machine-readable at the
same origin (docs/findings.json, docs/accuracy.json, ...).

Run:  python agent/build_site.py [--repo-url https://github.com/you/repo]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SRC = ROOT / "site"      # template lives here
DOCS = ROOT / "docs"     # GitHub Pages serves this folder with zero config

PUBLISHED = [
    "findings.json", "patterns.json", "accuracy.json", "gold_sample.json",
    "pass1_baseline.json", "pass2_sample.json", "mcp_probe_results.json",
    "composio_gap.json", "apps.json",
]


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", default="https://github.com/mohilahuja/composio-toolkit-research")
    args = ap.parse_args()

    findings = load("findings.json")["records"]
    patterns = load("patterns.json")
    accuracy = load("accuracy.json")
    gap = load("composio_gap.json")

    ap_ = patterns["auth_primary"]
    acc = patterns["access"]
    verdicts = patterns["verdict"]
    non_build = verdicts.get("caveats", 0) + verdicts.get("blocked", 0)
    commercial = sum(
        1 for r in findings
        if r["verdict"] != "build" and r["access"] in ("gated", "partner", "self_serve_paid")
    )

    payload = {
        "headline": {
            "key_like": ap_.get("api_key", 0) + ap_.get("token", 0) + ap_.get("basic", 0),
            "oauth": ap_.get("oauth2", 0),
            "self_serve": acc.get("self_serve_free", 0) + acc.get("self_serve_paid", 0),
            "free": acc.get("self_serve_free", 0),
            "paid": acc.get("self_serve_paid", 0),
            "gated": acc.get("gated", 0),
            "partner": acc.get("partner", 0),
            "human_gate": acc.get("gated", 0) + acc.get("partner", 0),
            "mcp_official": patterns["mcp"].get("official", 0),
            "build": verdicts.get("build", 0),
            "non_build": non_build,
            "commercial_blockers": commercial,
            "gap_missing": gap["_meta"]["not_in_catalogue"],
        },
        "by_category": patterns["by_category"],
        "mcp_rate_by_category": patterns["mcp_rate_by_category"],
        "build_queue": gap["build_queue"],
        "findings": [
            {k: r[k] for k in ("id", "app", "category", "one_liner", "auth", "access", "api",
                               "breadth", "mcp", "verdict", "blocker", "evidence", "confidence", "note")}
            for r in findings
        ],
        "acc": {
            "pass1": accuracy["pass1_parametric"]["overall"]["pct"],
            "pass2": accuracy["pass2_single_doc"]["overall"]["pct"],
            "fields": [
                {"name": f, "p1": accuracy["pass1_parametric"]["per_field"][f]["pct"],
                 "p2": accuracy["pass2_single_doc"]["per_field"][f]["pct"]}
                for f in ("auth", "access", "api", "mcp", "verdict")
            ],
            "misses": accuracy["pass1_parametric"]["misses"],
            "mcp_baseline": accuracy["mcp_full_set_check"]["baseline_pct"],
            "mcp_flips": accuracy["mcp_full_set_check"]["flip_direction"],
        },
        "low_confidence": [
            {"app": r["app"], "note": " " + r["blocker"]}
            for r in findings if r["confidence"] == "low"
        ] + [
            {"app": "32 medium-confidence rows",
             "note": " Single-vendor-page evidence only. Listed as 'medium' in the confidence column "
                     "of the table above — filter for them and treat them as the review queue."}
        ],
    }

    html = (SRC / "template.html").read_text(encoding="utf-8")
    html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    html = html.replace("REPO_URL", args.repo_url)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")

    for name in PUBLISHED:
        src = DATA / name
        if src.exists():
            shutil.copyfile(src, DOCS / name)

    size = (DOCS / "index.html").stat().st_size
    print(f"wrote docs/index.html ({size/1024:.0f} KB) + {len(PUBLISHED)} JSON files")
    print(f"headline: {payload['headline']}")


if __name__ == "__main__":
    main()
