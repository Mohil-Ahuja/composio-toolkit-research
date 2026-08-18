"""
research_agent.py — the three-loop toolkit-buildability researcher.

    python agent/research_agent.py --pass 1          # parametric baseline, no tools
    python agent/research_agent.py --pass 2          # retrieval-grounded (web_search + web_fetch)
    python agent/research_agent.py --pass 3          # adversarial verifier over pass 2
    python agent/research_agent.py --pass 2 --only 4,15,50 --force

Design notes, because the loops are the whole point:

  Loop 1 (baseline)  No tools at all. The model answers from weights. This exists to be
                     graded, not to be shipped — it is the control that shows how much the
                     retrieval is actually worth. Run it first, never overwrite it.

  Loop 2 (grounded)  Server-side web_search + web_fetch, one app per request, structured
                     JSON out. Every field must carry an evidence URL the model actually
                     opened. A field with no evidence must be returned as null, not guessed;
                     "not stated in the docs" is a legitimate and useful answer.

  Loop 3 (verify)    A fresh request that never sees loop 1. It gets the loop-2 record and is
                     told to attack it: find the field most likely to be wrong and check that
                     one hard. Adversarial framing matters — asking a model to "double-check
                     its work" mostly produces agreement, whereas asking it to find the weakest
                     claim produces disagreements you can act on. Disagreements are written to
                     data/disagreements.json for a human, not auto-applied.

  Deterministic loops (no LLM) live in verify_probe.py and composio_gap.py. Those are where
  the MCP column actually got fixed: model weights lag vendor launches, so the reliable way
  to answer "does vendor X ship an MCP server" is to GET vendor X's docs and read the body.

Cost control: results are checkpointed per app, so a crashed or rate-limited run resumes
instead of re-paying. --only re-runs a subset when a row looks wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MODEL = "claude-opus-5"
# Dynamic-filtering server-tool variants. On models older than Opus 4.6 / Sonnet 4.6 these
# fall back to web_search_20250305 / web_fetch_20250910.
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
WEB_FETCH = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8,
             "max_content_tokens": 12000}

RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "one_liner": {"type": "string", "description": "What the app does, one sentence."},
        "auth": {
            "type": "array",
            "items": {"type": "string", "enum": [
                "oauth2", "api_key", "basic", "token", "custom", "none", "unknown"]},
            "description": "Credential types a developer actually uses against the public API, most common first.",
        },
        "access": {
            "type": "string",
            "enum": ["self_serve_free", "self_serve_paid", "gated", "partner", "unknown"],
            "description": (
                "self_serve_free: credentials on a free plan/trial/dev account, no human in the loop. "
                "self_serve_paid: no approval needed but a paid plan is. "
                "gated: admin approval, app review, or existing-customer contract. "
                "partner: partnership or contact-sales negotiation."
            ),
        },
        "api": {"type": "string", "enum": ["rest", "graphql", "both", "none", "unknown"]},
        "breadth": {"type": "string", "enum": ["narrow", "medium", "broad", "unknown"]},
        "mcp": {"type": "string", "enum": ["official", "community", "none", "unknown"]},
        "verdict": {"type": "string", "enum": ["build", "caveats", "blocked", "unknown"]},
        "blocker": {"type": "string", "description": "The single thing standing between a developer and a working toolkit. Empty string if nothing does."},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URLs actually opened this turn that support the answers above. Never invent one.",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "note": {"type": "string", "description": "Anything a human should know: doc quotes, contradictions, rebrands, dead links."},
    },
    "required": ["one_liner", "auth", "access", "api", "breadth", "mcp", "verdict",
                 "blocker", "evidence", "confidence", "note"],
    "additionalProperties": False,
}

SYSTEM_BASE = """You research whether a SaaS app could become an agent-callable toolkit today.

You are answering for a team that builds these integrations, so be concrete and be honest.
The rules that matter:

- "Has an API" is not the question. The question is whether a developer can get working
  credentials without a human on the other side, and what breaks if they cannot.
- Distinguish getting a key from paying for a plan from needing approval. A 14-day trial that
  issues a token is self-serve. An enterprise contract that issues one is not.
- A paywall or a partner gate is a correct finding, not a failure. Report it plainly.
- Never invent an endpoint, a doc URL, or an MCP server. If you do not know, say unknown and
  set confidence low. An honest unknown is worth more than a confident guess, because a wrong
  row costs an engineer a day.
"""

SYSTEM_PASS1 = SYSTEM_BASE + """
You have NO tools this pass. Answer from what you already know. Do not pretend to have
checked anything: leave `evidence` empty and set confidence accordingly. This pass is a
control that will be graded against verified data.
"""

SYSTEM_PASS2 = SYSTEM_BASE + """
Use web_search and web_fetch. Prefer the vendor's own developer documentation over blogs,
directories, and API-comparison sites; use those only to locate the vendor page.

Two failure modes to avoid, both observed in practice:
1. Authentication docs almost never state pricing or gating. If the auth page is silent on
   access, that is not an answer — search the pricing or help-centre page before deciding.
2. Doc URLs rot, and vendors get acquired or rebrand. If a fetch 404s or redirects to a
   different company, follow the redirect and say so in `note` — the redirect is often the
   most interesting finding about that app.
"""

SYSTEM_PASS3 = SYSTEM_BASE + """
You are verifying someone else's research, adversarially. You will be given a completed
record. Your job is NOT to agree with it.

Pick the field most likely to be wrong — usually `access` (gating is rarely stated on the
page the first pass read) or `mcp` (vendors ship MCP servers faster than anyone updates their
notes) — and check that one hard against primary sources. Then return the corrected record.
Where you changed something, say so in `note` and cite the URL that made you change it.
If everything holds up, return it unchanged and say what you checked.
"""


def client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. `ant auth login` also works — the SDK reads that profile.",
              file=sys.stderr)
    return anthropic.Anthropic()


def ask(cli: anthropic.Anthropic, system: str, prompt: str, use_tools: bool) -> tuple[dict, dict]:
    """One app, one structured record. Returns (record, usage). Handles pause_turn."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tools = [WEB_SEARCH, WEB_FETCH] if use_tools else []
    usage = {"input_tokens": 0, "output_tokens": 0, "server_tool_use": 0}

    for _ in range(6):  # bounded pause_turn resumes
        with cli.messages.stream(
            model=MODEL,
            max_tokens=8000,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": RECORD_SCHEMA},
            },
        ) as stream:
            response = stream.get_final_message()

        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens
        server_use = getattr(response.usage, "server_tool_use", None)
        if server_use is not None:
            usage["server_tool_use"] += getattr(server_use, "web_search_requests", 0) or 0

        if response.stop_reason == "refusal":
            raise RuntimeError(f"refused: {response.stop_details}")
        if response.stop_reason == "pause_turn":
            # Long server-tool turn paused; resend so it can continue where it left off.
            messages.append({"role": "assistant", "content": response.content})
            continue

        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text), usage

    raise RuntimeError("turn still paused after 6 resumes")


def prompt_for(app: dict, prior: dict | None = None) -> str:
    base = (f"App: {app['app']}\nCategory: {app['category']}\n"
            f"Vendor hint from the brief: {app['hint']}\n")
    if prior is None:
        return base + "\nResearch it and return the record."
    return (base + "\nA previous pass produced this record:\n"
            + json.dumps(prior, indent=2)
            + "\n\nAttack it. Return the corrected record.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_no", type=int, choices=[1, 2, 3], required=True)
    ap.add_argument("--only", help="comma-separated app ids to (re)run")
    ap.add_argument("--force", action="store_true", help="ignore existing checkpoints")
    args = ap.parse_args()

    apps = json.loads((DATA / "apps.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        apps = [a for a in apps if a["id"] in wanted]

    out_path = DATA / f"agent_pass{args.pass_no}.json"
    done: dict[str, dict] = {}
    if out_path.exists() and not args.force:
        done = {str(r["id"]): r for r in json.loads(out_path.read_text(encoding="utf-8"))["records"]}

    prior_path = DATA / "agent_pass2.json"
    priors: dict[str, dict] = {}
    if args.pass_no == 3:
        if not prior_path.exists():
            sys.exit("pass 3 needs data/agent_pass2.json — run --pass 2 first")
        priors = {str(r["id"]): r for r in json.loads(prior_path.read_text(encoding="utf-8"))["records"]}

    system = {1: SYSTEM_PASS1, 2: SYSTEM_PASS2, 3: SYSTEM_PASS3}[args.pass_no]
    cli = client()
    totals = {"input_tokens": 0, "output_tokens": 0, "server_tool_use": 0}
    disagreements: list[dict] = []

    for app in apps:
        key = str(app["id"])
        if key in done:
            continue
        prior = priors.get(key) if args.pass_no == 3 else None
        try:
            rec, usage = ask(cli, system, prompt_for(app, prior), use_tools=args.pass_no != 1)
        except Exception as exc:                       # keep going; a dead row is data too
            print(f"  !! {app['app']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        for k in totals:
            totals[k] += usage[k]
        rec = {"id": app["id"], "app": app["app"], "category": app["category"], **rec}
        done[key] = rec

        if prior:
            changed = [f for f in ("auth", "access", "api", "mcp", "verdict")
                       if prior.get(f) != rec.get(f)]
            if changed:
                disagreements.append({"app": app["app"], "fields": changed,
                                      "pass2": {f: prior.get(f) for f in changed},
                                      "pass3": {f: rec.get(f) for f in changed}})

        flag = "!" if rec["confidence"] == "low" else " "
        print(f"{flag} {app['id']:>3} {app['app']:<26} {rec['auth'][0]:<8} "
              f"{rec['access']:<16} mcp={rec['mcp']:<9} {rec['verdict']}")

        # checkpoint after every app — a rate limit should never cost a completed row
        out_path.write_text(json.dumps({
            "_meta": {"pass": args.pass_no, "model": MODEL,
                      "generated": datetime.now(timezone.utc).isoformat(),
                      "usage": totals},
            "records": sorted(done.values(), key=lambda r: r["id"]),
        }, indent=2), encoding="utf-8")

    if disagreements:
        (DATA / "disagreements.json").write_text(
            json.dumps({"_meta": {"n": len(disagreements),
                                  "note": "Pass 3 disagreed with pass 2 here. Adjudicate by hand — do not auto-apply."},
                        "records": disagreements}, indent=2), encoding="utf-8")
        print(f"\n{len(disagreements)} disagreements written to data/disagreements.json for human review")

    # List pricing for the configured model, 2026-06: $5 / $25 per Mtok in / out.
    cost = totals["input_tokens"] / 1e6 * 5 + totals["output_tokens"] / 1e6 * 25
    print(f"\npass {args.pass_no}: {len(done)} records  "
          f"in={totals['input_tokens']:,} out={totals['output_tokens']:,} "
          f"searches={totals['server_tool_use']}  ~${cost:.2f} (model tokens only)")


if __name__ == "__main__":
    main()
