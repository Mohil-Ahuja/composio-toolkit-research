# Can we build a toolkit for it?

Research pipeline + case study for the Composio AI Product Ops take-home: **100 apps**, each
assessed for whether it could become an agent-callable toolkit today — auth method, whether a
developer can get credentials without asking permission, API surface and breadth, official MCP
status, a buildability verdict, and the evidence URL behind every call.

- **Live case study:** https://mohil-ahuja.github.io/composio-toolkit-research/
- **Source:** https://github.com/Mohil-Ahuja/composio-toolkit-research
- **Findings, machine-readable:** [`data/findings.json`](data/findings.json)
- **Accuracy scoring:** [`data/accuracy.json`](data/accuracy.json)

## Headline findings

| | |
|---|---|
| Static keys/tokens vs OAuth2 as the *primary* credential | **64 : 31** |
| Self-serve credentials (free plan/trial or paid plan, no approval) | **82 / 100** |
| Need a human (admin approval, app review, or partnership) | **18 / 100** |
| Ship an **official** MCP server (verified live) | **57 / 100** |
| Buildable today / caveats / blocked | **71 / 27 / 2** |
| Of the 29 non-"build" apps, blocked by commercial gates rather than technical ones | **23** |
| Not in Composio's public toolkit catalogue | **43 / 100** |

Access difficulty is a *category* property: developer infra, data/SEO and productivity are 100%
self-serve; marketing/ads and finance sit at 50%, and those same two categories are where MCP
adoption collapses (20% and 40%, vs 100% for dev infra). Those are the categories where a
partnerships conversation buys more than an engineer does.

## How to run the research agent

```bash
pip install -r requirements.txt
```

### Deterministic loops — no API key needed

These ran exactly as committed; their JSON is script output.

```bash
python agent/verify_probe.py    # fetch 73 candidate MCP URLs, confirm only if 2xx AND the body
                                # actually discusses MCP  -> data/mcp_probe_results.json
python agent/composio_gap.py    # check all 100 against composio.dev/toolkits/<slug>, rank the
                                # gap by how little stands in the way -> data/composio_gap.json
python agent/analyze.py         # cluster counts + pass-1/pass-2 scoring against the gold sample
                                # -> data/patterns.json, data/accuracy.json
python agent/build_site.py      # rebuild the case study from the data -> docs/index.html
```

### Model loops — needs `ANTHROPIC_API_KEY`

```bash
export ANTHROPIC_API_KEY=...
python agent/research_agent.py --pass 1              # parametric baseline, no tools (the control)
python agent/research_agent.py --pass 2              # web_search + web_fetch, structured JSON out
python agent/research_agent.py --pass 3              # adversarial verifier over pass 2
python agent/research_agent.py --pass 2 --only 4,50 --force   # re-run specific apps
```

Results checkpoint per app, so a rate limit or crash resumes instead of re-paying. Pass 3 writes
`data/disagreements.json` for a human to adjudicate — disagreements are never auto-applied.

### Optional: push the findings out through Composio

```bash
export COMPOSIO_API_KEY=...
python agent/export_composio.py --sheet <spreadsheet_id> --slack-channel '#product-ops'
python agent/export_composio.py          # dry run: prints the summary, no network
```

## The pipeline

| Loop | What it does | Output |
|---|---|---|
| 1 · control | Claude Opus 5, **no tools**, answering from weights. Never shipped — it exists to be graded so the retrieval has something to beat. | `pass1_baseline.json` |
| 2 · retrieval | One app per request, server-side `web_search` + `web_fetch`, structured JSON, every field carrying a URL the model actually opened. "Not stated in the docs" is a legal answer. | 100 records + evidence |
| 3 · adversarial | Fresh request that never sees loop 1, told to find the field *most likely wrong* rather than to double-check. Reviewing your own work mostly produces agreement. | `disagreements.json` |
| 4 · deterministic | Direct HTTP probe of candidate MCP endpoints. Status alone is not evidence — SPA docs sites return 200 for paths that don't exist. | `mcp_probe_results.json` |
| 5 · deterministic | Diff against Composio's public catalogue, ranked into a build queue. | `composio_gap.json` |

The design decision that mattered was not the prompt. It was noticing which questions a language
model is bad at — "does this vendor ship an MCP server?" — and taking those away from it.

## Accuracy

22 apps were verified by hand against 2+ independent sources, spanning all ten categories and
deliberately over-weighted toward the rows most likely to break (long-tail vendors, recent
rebrands, ambiguous gating). Five graded fields per app = 110 judgements per pass.

| Pass | Accuracy |
|---|---|
| Loop 1 — no retrieval | **72.7%** |
| Loop 2 — primary docs only | **76.4%** |
| Loops 3–5 — verified | 100% *by construction — the gold set is the answer key* |

Per field, baseline → retrieval: auth 86.4 → 90.9, api 90.9 → 95.5, verdict 77.3 → 81.8,
mcp 50.0 → 54.5, and **access 59.1 → 59.1 — no improvement at all.**

That flat line is the most useful result here. Authentication docs do not state pricing or
gating, so the page an agent naturally fetches cannot answer the question the business actually
asks. It needs a second, different source — pricing pages, help centres — which is now an
explicit instruction in the loop-2 prompt.

The MCP column is measured on the **full 100**, not a sample, because the endpoint probe checks
every row: the model prior was correct on 68/100, and of the 32 corrections, 27 were the model
*under*-counting official MCP servers. Vendors shipped MCP faster than training data records it.

## Honesty

- **Paygent Connect defeated me.** Nothing branded that way surfaced across three searches and
  two fetches. It stays `unknown` / confidence `low` rather than being filled with a guess.
- **32 rows rest on a single vendor page** and are marked `medium` confidence — the most likely
  place a second reviewer finds an error, labelled so nobody has to guess which ones.
- **Access is a four-bucket judgement over a messy reality.** Squarespace gates by plan *and* by
  API; Otter's MCP is free while its REST API is enterprise-only. The `note` field carries what
  the bucket drops.
- **Provenance:** I had no API key for this submission, so loops 1–3 were executed by Claude
  Opus 5 inside Claude Code using the same search/fetch tools and the same three prompts, one app
  at a time, writing to the same files. `research_agent.py` is that pipeline as a standalone
  script — written and reviewed, but not billed against a key. Loops 4 and 5 ran as committed.
- Where an app is paywalled or partner-gated, that *is* the finding. PitchBook, LinkedIn Ads and
  Consensus are correct rows, not failures.

## Repo layout

```
data/     apps.json (the 100)  ·  pass1_baseline.json (control)  ·  pass2_sample.json
          gold_sample.json (hand-verified)  ·  findings.json (the answer)
          patterns.json  ·  accuracy.json  ·  mcp_probe_results.json  ·  composio_gap.json
agent/    research_agent.py  ·  verify_probe.py  ·  composio_gap.py
          analyze.py  ·  build_site.py  ·  export_composio.py
site/     template.html (the page source; data is injected, never typed)
docs/     index.html (generated)  ·  *.json (copied so the deployed site is machine-readable)
```

Every number on the case-study page is computed from the JSON beside it by `analyze.py` and
injected by `build_site.py`. Nothing on the page is typed in by hand.
