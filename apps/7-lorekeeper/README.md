# LoreKeeper ✦ Creator Super-Fan D2C Engine

Creators own their monetization instead of relying on platform payouts. LoreKeeper
turns your audience into a **fan graph** — who your superfans are, how deep their
engagement runs, and what they'll pay — then lets an **AI persona in your voice**
run the fan business. The moat is the fan-engagement graph.

Self-contained demo. **Python 3 stdlib only.** No pip, no apt, no browsers, no Rust.

## Run

```bash
./run.sh            # foreground
./run.sh -d         # background (daemon)
./run.sh status     # RUNNING / STOPPED
./run.sh stop       # stop the daemon
```

Then open **http://localhost:8807**

On first launch it seeds `lorekeeper.db` (SQLite) with ~20 mock superfans and a few drops.
Delete `lorekeeper.db` any time to reseed fresh.

## Features

1. **Fan Constellation** (hero) — animated canvas of superfans orbiting you.
   Node size = lifetime spend, glow = affinity, red ring = going cold. Hover for a
   profile card; click a fan to talk to them.
2. **AI Persona Chat** — pick a fan, send a message, the persona replies **in the
   creator's configured voice**. Real Claude (`claude-sonnet-4-6`) if a key is present,
   deterministic mock otherwise. "Play voice" uses ElevenLabs (if keyed) or the
   browser's `speechSynthesis`.
3. **Drops Manager** — create an exclusive drop, get **projected revenue** from
   eligible top-tier fans (weighted by affinity & willingness-to-pay), with the
   **5% platform-fee** widget.
4. **Fan Intelligence** — top spenders, at-risk (cooling) fans, willingness-to-pay bands.
5. **Ambient Mode (2036)** — simulates the always-on persona autonomously detecting
   the most valuable at-risk fan and re-engaging them in your voice.

## Integrations (auto-detected, optional)

Reads `~/creator-os/.env`:

| Key | Enables | Fallback |
|-----|---------|----------|
| `ANTHROPIC_API_KEY`  | real Claude persona replies | deterministic mock |
| `ELEVENLABS_API_KEY` | `/api/tts` voiced replies    | browser speechSynthesis |

Badges in the header show which mode is live.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/api/state`   | persona, fans, drops, intelligence, integration flags |
| POST | `/api/chat`    | `{fan_id, message}` → in-voice persona reply |
| POST | `/api/drop`    | `{title, price, tier_min, create}` → revenue projection (+ optional create) |
| POST | `/api/persona` | update creator voice/tone |
| POST | `/api/tts`     | `{text}` → audio/mpeg (ElevenLabs) or `{ok:false}` |
| GET  | `/api/ambient` | autonomous at-risk re-engagement simulation |

## Files

- `server.py` — http.server backend, SQLite fan-graph, Claude/ElevenLabs urllib calls
- `index.html` — single-file holographic UI (neon magenta + cyan, glassmorphism, canvas)
- `run.sh` — lifecycle wrapper
- `lorekeeper.db` — created on first run (git-ignore / delete to reseed)
