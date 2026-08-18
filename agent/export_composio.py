"""
export_composio.py — optional last mile: push the findings out through Composio.

The research is only useful if it lands where the team already works, so this writes the
100 rows to a Google Sheet and posts the headline + build queue to Slack, using Composio
as the auth layer rather than hand-rolling two OAuth flows.

    export COMPOSIO_API_KEY=...
    python agent/export_composio.py --sheet <spreadsheet_id> --slack-channel '#product-ops'

Not run for this submission — I had no Composio key — so treat it as the integration path,
not as a verified artifact. Tool slugs and argument names should be confirmed against
`composio.tools.get(...)` for your account before a real run; they differ per toolkit
version and I would rather flag that than quietly ship a wrong slug.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
USER_ID = os.environ.get("COMPOSIO_USER_ID", "default")

HEADER = ["id", "app", "category", "auth", "access", "api", "breadth",
          "mcp", "verdict", "blocker", "confidence", "evidence"]


def rows() -> list[list[str]]:
    findings = json.loads((DATA / "findings.json").read_text(encoding="utf-8"))["records"]
    out = [HEADER]
    for r in findings:
        out.append([
            str(r["id"]), r["app"], r["category"], "/".join(r["auth"]), r["access"],
            r["api"], r["breadth"], r["mcp"], r["verdict"], r["blocker"],
            r["confidence"], " ".join(r["evidence"]),
        ])
    return out


def summary() -> str:
    patterns = json.loads((DATA / "patterns.json").read_text(encoding="utf-8"))
    gap = json.loads((DATA / "composio_gap.json").read_text(encoding="utf-8"))
    queue = ", ".join(row["app"] for row in gap["build_queue"][:8])
    return (
        f"*100-app toolkit buildability sweep*\n"
        f"• {patterns['verdict'].get('build', 0)} buildable today, "
        f"{patterns['verdict'].get('caveats', 0)} with access friction, "
        f"{patterns['verdict'].get('blocked', 0)} blocked\n"
        f"• API keys/tokens beat OAuth2 "
        f"{patterns['auth_primary'].get('api_key', 0) + patterns['auth_primary'].get('token', 0)}:"
        f"{patterns['auth_primary'].get('oauth2', 0)} as the primary credential\n"
        f"• {patterns['mcp'].get('official', 0)}/100 already ship an official MCP server\n"
        f"• {gap['_meta']['not_in_catalogue']} of the 100 are not in the Composio catalogue. "
        f"Easiest first: {queue}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", help="Google Sheets spreadsheet id to write into")
    ap.add_argument("--slack-channel", help="Slack channel for the summary")
    args = ap.parse_args()

    if not args.sheet and not args.slack_channel:
        print(summary())  # dry run: no key, no SDK, no network
        return

    from composio import Composio  # imported late so the rest of the repo runs without it

    composio = Composio(api_key=os.environ["COMPOSIO_API_KEY"])

    if args.sheet:
        composio.tools.execute(
            "GOOGLESHEETS_BATCH_UPDATE",
            user_id=USER_ID,
            arguments={
                "spreadsheet_id": args.sheet,
                "sheet_name": "findings",
                "first_cell_location": "A1",
                "values": rows(),
            },
        )
        print(f"wrote {len(rows()) - 1} rows to sheet {args.sheet}")

    if args.slack_channel:
        composio.tools.execute(
            "SLACK_SEND_MESSAGE",
            user_id=USER_ID,
            arguments={"channel": args.slack_channel, "text": summary()},
        )
        print(f"posted summary to {args.slack_channel}")


if __name__ == "__main__":
    main()
