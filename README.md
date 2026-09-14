# Venture Lab — 10 Future-Proof Vertical-AI Webapps

Ten vertical AI micro-SaaS apps, each attacking a real, researched market gap that today's tools
ignore — and each **future-proofed to the "apps die in 10 years" thesis**: every one is a
proprietary-data webapp designed to graduate into an always-on **ambient agent**. See
`MASTER_PLAN.md` for the full thesis, research, moats, revenue models and 10-year evolution.

Built overnight 2026-09-14 by a wave of subagents. Stack: **Python 3 stdlib** backend
(`http.server` + `sqlite3` + `urllib`) + **single-file futuristic HTML/canvas** UI, each with its
own colour system. Live **Claude (`claude-sonnet-4-6`)** wired from `~/creator-os/.env` (with mock
fallback); **ElevenLabs** voice where relevant (#1, #7). *(Rust was preferred but isn't installed
and RAM was tight — Python was the pragmatic call; hot paths can be ported later.)*

## Run the whole portfolio
```bash
./demo.sh up       # start all 10 (detached) and print URLs
./demo.sh status   # which are up
./demo.sh down     # stop all
```
Or run one: `cd apps/1-tradepilot && ./run.sh` → open its localhost port.

| # | App | Port | Segment nobody serves well | Colour world |
|---|-----|------|-----------------------------|--------------|
| 1 | **TradePilot** | 8801 | solo trades job→cash (voice) | amber / graphite |
| 2 | **EmberHealth** | 8802 | PCOS/endo/fibro flare prediction | rose / plum |
| 3 | **ClaimGuard** | 8803 | pre-submission medical billing-error catch | teal / white |
| 4 | **FreightNormal** | 8804 | freight quote normalization | cyan / navy |
| 5 | **CloseLoop** | 8805 | real-estate transaction reconciliation | slate / gold |
| 6 | **LedgerBridge** | 8806 | accounting-tool integration glue | emerald / charcoal |
| 7 | **LoreKeeper** | 8807 | creator superfan D2C + AI persona (voice) | neon magenta / cyan |
| 8 | **CompliAgent** | 8808 | SMB regulatory-compliance radar | indigo / steel |
| 9 | **FocusForge** | 8809 | ADHD executive-function OS | electric lime / violet |
| 10 | **Mesa** | 8810 | mid-market ambient ops (dogfoods **ACE**) | violet / space |

Every app ships: `server.py`, `index.html`, `run.sh` (fg/`-d`/stop/status), `README.md`, an SQLite
DB seeded with realistic mock data on first run, and an **"Ambient mode (2036)" preview** panel
showing how it becomes an autonomous agent.

## Why these win (from the research)
- Vertical AI micro-SaaS ≈ $95B, growing 2–3× horizontal SaaS, **59% AI attach** vs 20–35%.
- Moat = **proprietary data + workflow depth**, not model features (inference is a commodity input).
- The whitespace is **micro-segments** big players ignore; shipping fast into one is 2.4× more likely
  to hit profitability.
