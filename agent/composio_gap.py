"""
composio_gap.py — turns the research into a build queue.

Knowing an app is buildable is only half an answer. The question Product Ops actually
asks next is "do we already have it?". This script checks each of the 100 apps against
Composio's public toolkit catalogue by probing https://composio.dev/toolkits/<slug>
(200 = a toolkit page exists, 404 = it does not), trying a few slug spellings per app.

Output: data/composio_gap.json — existing coverage, plus the gap list ranked by how easy
the app is to build (verdict == "build" and access is self-serve = top of the queue).

Caveat recorded in the output: a 200 proves a catalogue page exists, not that the toolkit
is deep or current. It is a coverage signal, not a quality signal.

Run:  python agent/composio_gap.py
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASE = "https://composio.dev/toolkits/{}"
HEADERS = {"User-Agent": "composio-research-agent/1.0"}

# Hand-written overrides where a mechanical slug will not match the catalogue.
OVERRIDES = {
    "Monday.com": ["monday"],
    "Zoho CRM": ["zoho_crm", "zohocrm", "zoho"],
    "Zoho Cliq": ["zoho_cliq", "zohocliq"],
    "Lark (Larksuite)": ["lark", "larksuite", "feishu"],
    "Google Ads": ["google_ads", "googleads"],
    "Meta Ads": ["meta_ads", "facebook_ads", "metaads"],
    "LinkedIn Ads": ["linkedin_ads", "linkedin"],
    "Threads (Meta)": ["threads"],
    "WhatsApp Business": ["whatsapp", "whatsapp_business"],
    "Magento (Adobe Commerce)": ["magento", "adobe_commerce"],
    "Salesforce Commerce Cloud": ["salesforce_commerce_cloud", "commerce_cloud"],
    "Amazon Selling Partner": ["amazon", "amazon_sp_api", "sp_api"],
    "MongoDB Atlas": ["mongodb", "mongodb_atlas"],
    "Otter AI": ["otter", "otter_ai"],
    "YouTube Transcript": ["youtube", "transcriptapi"],
    "Mermaid CLI": ["mermaid"],
    "systeme.io": ["systeme", "systemeio"],
    "Paygent Connect": ["paygent", "nmi"],
    "Waterfall.io": ["waterfall"],
    "Help Scout": ["helpscout", "help_scout"],
    "QuickBooks": ["quickbooks", "qbo"],
    "Jira": ["jira", "atlassian"],
    "Twenty": ["twenty", "twenty_crm"],
    "NotebookLM": ["notebooklm"],
    "Bright Data": ["brightdata", "bright_data"],
    "SE Ranking": ["seranking", "se_ranking"],
}


def slugs(app: str) -> list[str]:
    if app in OVERRIDES:
        return OVERRIDES[app]
    base = re.sub(r"[^a-z0-9]+", "_", app.lower()).strip("_")
    out = [base]
    if "_" in base:
        out.append(base.replace("_", ""))
    out.append(f"{base}_mcp")  # the catalogue also carries <name>_mcp slugs
    return out


def check(rec: dict) -> dict:
    for slug in slugs(rec["app"]):
        try:
            r = requests.get(BASE.format(slug), headers=HEADERS, timeout=20)
        except Exception:
            continue
        if r.status_code == 200:
            return {"app": rec["app"], "exists": True, "slug": slug, "url": BASE.format(slug)}
    return {"app": rec["app"], "exists": False, "slug": None, "url": None}


def main() -> None:
    findings = json.loads((DATA / "findings.json").read_text(encoding="utf-8"))["records"]
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, findings))

    by_app = {r["app"]: r for r in results}
    covered = [r for r in results if r["exists"]]
    missing = [r for r in results if not r["exists"]]

    def rank(rec: dict) -> tuple:
        # easiest first: build verdict, self-serve free access, official MCP already exists
        return (
            {"build": 0, "caveats": 1, "blocked": 2}[rec["verdict"]],
            {"self_serve_free": 0, "self_serve_paid": 1, "gated": 2, "partner": 3}[rec["access"]],
            0 if rec["mcp"] == "official" else 1,
        )

    gap = sorted(
        (r for r in findings if not by_app[r["app"]]["exists"]),
        key=rank,
    )

    payload = {
        "_meta": {
            "checked": len(results),
            "existing_toolkits": len(covered),
            "not_in_catalogue": len(missing),
            "method": "HTTP GET https://composio.dev/toolkits/<slug>, 200 = page exists. Slug variants tried per app.",
            "caveat": "A 200 proves a catalogue page exists, not that the toolkit is deep or current. Coverage signal, not quality signal. Slug mismatches would show as false negatives.",
        },
        "existing": sorted(r["app"] for r in covered),
        "build_queue": [
            {
                "rank": i + 1, "app": r["app"], "category": r["category"],
                "auth": r["auth"][0], "access": r["access"], "mcp": r["mcp"],
                "verdict": r["verdict"], "blocker": r["blocker"],
            }
            for i, r in enumerate(gap)
        ],
    }
    (DATA / "composio_gap.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"checked {len(results)} apps")
    print(f"already in the Composio catalogue: {len(covered)}")
    print(f"not found: {len(missing)}")
    print("\ntop 12 of the build queue (easiest first):")
    for row in payload["build_queue"][:12]:
        print(f"  {row['rank']:>2}. {row['app']:<24} {row['auth']:<8} {row['access']:<16} mcp={row['mcp']}")


if __name__ == "__main__":
    main()
