"""
verify_probe.py — Loop 3a: automated endpoint verification, no LLM involved.

Why this exists: the MCP column was the single least reliable field in the LLM passes
(50% correct on the gold sample, 72% across the whole set). Model weights lag vendor
launches by months, and vendors ship MCP servers faster than they ship anything else.

So instead of asking a model "does vendor X have an MCP server?", this script asks the
vendor's own docs site. For each candidate URL it records:
  - the HTTP status after following redirects, and the final URL (catches rebrands
    and acquisitions: fanbasis.com -> commas.com, coda.io/developers -> superhuman.com)
  - how many times "mcp" and "model context protocol" appear in the body

Status alone is not evidence: docs sites built as SPAs happily return 200 for paths that
do not exist. A page only counts as confirmation if it returns 2xx AND the body actually
talks about MCP. Everything else is reported as unconfirmed and left as "none" in the
dataset rather than guessed.

Run:  python agent/verify_probe.py
Out:  data/mcp_probe_results.json
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CANDIDATES = DATA / "mcp_candidates.tsv"
OUT = DATA / "mcp_probe_results.json"

HEADERS = {"User-Agent": "composio-research-agent/1.0 (+toolkit buildability research)"}
TIMEOUT = 25
CONFIRM_MIN_HITS = 3  # body must mention MCP at least this many times to count


def probe(app: str, url: str) -> dict:
    row = {"app": app, "url": url, "status": None, "final_url": None,
           "mcp_hits": 0, "mcp_full_hits": 0, "confirmed": False, "error": None}
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        body = r.text or ""
        row["status"] = r.status_code
        row["final_url"] = r.url
        row["redirected"] = (r.url.rstrip("/") != url.rstrip("/"))
        row["mcp_hits"] = len(re.findall(r"mcp", body, re.I))
        row["mcp_full_hits"] = len(re.findall(r"model context protocol", body, re.I))
        row["confirmed"] = (
            200 <= r.status_code < 300
            and (row["mcp_hits"] >= CONFIRM_MIN_HITS or row["mcp_full_hits"] >= 1)
        )
    except Exception as exc:  # network failures are data too — a doc site that blocks
        row["error"] = f"{type(exc).__name__}: {exc}"  # bots is itself a finding
    return row


def main() -> None:
    candidates = []
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        app, url = line.split("\t", 1)
        candidates.append((app, url))

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: probe(*t), candidates))

    confirmed = [r for r in results if r["confirmed"]]
    redirects = [r for r in results if r.get("redirected") and r["status"] and r["status"] < 400]
    blocked = [r for r in results if r["status"] in (401, 403, 429) or r["error"]]

    payload = {
        "_meta": {
            "probed": len(results),
            "confirmed_official_mcp": len(confirmed),
            "unconfirmed": len(results) - len(confirmed),
            "bot_blocked_or_error": len(blocked),
            "rule": f"2xx AND (>= {CONFIRM_MIN_HITS} 'mcp' mentions OR >= 1 'model context protocol')",
        },
        "confirmed": sorted(r["app"] for r in confirmed),
        "bot_blocked": sorted(r["app"] for r in blocked),
        "redirect_findings": [
            {"app": r["app"], "from": r["url"], "to": r["final_url"]} for r in redirects
        ],
        "results": results,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"probed {len(results)} candidate MCP URLs")
    print(f"confirmed: {len(confirmed)}  unconfirmed: {len(results) - len(confirmed)}  blocked/error: {len(blocked)}")
    print("confirmed ->", ", ".join(sorted(r['app'] for r in confirmed)))


if __name__ == "__main__":
    main()
