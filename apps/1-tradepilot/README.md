# TradePilot — Job-to-Cash Cockpit for Solo Trades

A self-contained demo of an operating system for solo skilled-trades businesses
(plumbers, HVAC techs, electricians — 1–5 staff). A job comes in as a **photo +
voice note** → AI drafts an **itemized quote** → **schedule** → **invoice** →
**auto follow-up / payment**. Voice-first.

**The moat:** a per-trade **labor + parts pricing corpus** that compounds with
every job — the pricing edge sharpens with volume and travels with the operator.

**The vision:** *Ambient mode* — the product becomes an agent that answers the
customer's inbound calls/texts, prices from the corpus, and books the job with
zero taps from the owner. See the "Ambient Mode (2036 Preview)" panel.

## Stack
- **Python 3 stdlib only** — no pip, no external packages, tiny RAM footprint.
- `server.py`: `http.server` on **port 8801**, serves a single-file `index.html`.
- `sqlite3` for storage — seeded with ~12 realistic mock jobs on first run
  (`tradepilot.db` is created automatically).
- **Claude** (`claude-sonnet-4-6`, Anthropic API via `urllib`) drafts real quotes
  **if** `ANTHROPIC_API_KEY` is in `~/creator-os/.env`; otherwise a deterministic
  corpus-based **MOCK** fallback is used.
- **ElevenLabs** TTS (`/tts`, via `urllib`) narrates quotes **if**
  `ELEVENLABS_API_KEY` is present; otherwise the browser's `speechSynthesis` is used.
- Dictation uses the browser Web Speech API (graceful if unsupported).

## Run
```bash
./run.sh          # foreground (Ctrl-C to stop)
./run.sh -d       # detached -> /tmp/tradepilot.log, pid in /tmp/tradepilot.pid
./run.sh stop     # stop detached server
./run.sh status   # show status
```
Then open **http://localhost:8801**.

## Endpoints
| Method | Path            | Purpose |
|--------|-----------------|---------|
| GET    | `/`             | Single-file cockpit UI |
| GET    | `/api/state`    | Jobs, pipeline, pricing intel, money totals, capabilities |
| GET    | `/api/health`   | Liveness + which AI/TTS capabilities are active |
| POST   | `/api/quote`    | Draft an itemized quote (Claude if available, else corpus mock); saves job as **Quoted** |
| POST   | `/api/advance`  | Move a job one stage forward/back (`{job_id, direction:±1}`) |
| POST   | `/api/set_stage`| Drag-drop target stage (`{job_id, stage}`) |
| POST   | `/api/ambient`  | Simulate the ambient agent auto-replying to an inbound text |
| POST   | `/tts`          | ElevenLabs MP3 narration, or `{fallback:"speechSynthesis"}` |

## Features
1. **Job intake** — customer, title, photo URL, voice note (dictate or type) → AI quote.
2. **Pipeline board** — New → Quoted → Scheduled → Invoiced → Paid, with drag-drop
   or click-to-advance. Entering *Invoiced* auto-creates an invoice; *Paid* settles it.
3. **Invoice / money strip** — running Collected, Outstanding, 0.5% platform fee, Net.
4. **Pricing Intelligence** — learned avg ticket per trade from the growing corpus.
5. **Ambient mode preview** — the agent classifies an inbound text, quotes a range
   from the corpus, offers slots, and drafts the job hands-free.

## Notes
- Pure stdlib; nothing is installed. Validated with `python3 -m py_compile server.py`.
- Delete `tradepilot.db` to reseed from scratch.
