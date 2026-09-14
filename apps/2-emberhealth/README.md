# EmberHealth 🌸

**A flare-prediction OS for the chronic conditions mainstream health apps ignore** —
PCOS, endometriosis, and fibromyalgia. Diabetes and heart apps are everywhere; the
tens of millions living with these under-served, often-dismissed conditions get nothing.

EmberHealth closes that gap: log symptoms + triggers over time → build a personal
symptom/trigger graph → **predict the next flare** → generate a **doctor-ready report**.

> **Moat:** a longitudinal, condition-specific dataset plus a per-user flare model —
> something generic wellness apps can't retrofit.

## What it does

1. **Daily check-in** — condition picker (PCOS / endo / fibro), pain/fatigue/sleep/stress
   sliders, cycle day, and trigger chips (dairy, gluten, weather, stress, poor sleep…).
2. **Flare forecast** — a transparent 0–100 risk for the next 3 days with a clear gauge,
   plus the exact drivers behind the number (symptom momentum, sleep debt, active
   triggers, cycle phase).
3. **Symptom timeline** — a 30-day canvas chart (pain/fatigue/sleep) with flare markers.
4. **Pattern intelligence** — your top correlated triggers, e.g. *"dairy precedes 78% of
   your flares."*
5. **Insight & doctor-ready report** — an empathetic plain-English read and a structured
   clinical summary (real Claude if an API key is present, mock otherwise).
6. **Ambient mode (2036 preview)** — simulates a wearable stream, predicts a flare before
   you feel it, and pre-arranges care.

## Stack

Python 3 **stdlib only** (`http.server` + `sqlite3`). No pip, no ML libs, no browser.
The flare-risk model is a transparent weighted heuristic — every point of risk is
explained. On first run it seeds `emberhealth.db` with ~30 days of realistic, correlated
mock PCOS data (a genuine latent trigger/cycle pattern is baked in so the model has
something true to find).

**AI:** if `~/creator-os/.env` contains `ANTHROPIC_API_KEY`, insights/reports use a live
`claude-sonnet-4-6` call via `urllib`. Otherwise a warm mock fallback is used. The UI
badge shows which engine is active.

## Run

```bash
./run.sh          # foreground on http://localhost:8802
./run.sh -d       # detached (writes emberhealth.log + .emberhealth.pid)
./run.sh status   # running / stopped
./run.sh stop     # stop the detached server
```

## API

| Method | Path                         | Purpose                                        |
|--------|------------------------------|------------------------------------------------|
| GET    | `/`                          | single-file UI                                 |
| GET    | `/api/config`                | conditions, triggers, AI mode                  |
| GET    | `/api/state?condition=pcos`  | forecast + patterns + trend                    |
| POST   | `/api/log`                   | log/replace a day → returns new forecast       |
| POST   | `/api/insight`               | empathetic plain-English insight (AI)          |
| POST   | `/api/report`                | doctor-ready structured summary (AI)           |
| POST   | `/api/ambient`               | simulated wearable stream + pre-emptive plan   |

Port **8802**.

## ⚠️ Not medical advice

This is a demo. Predictions are illustrative, generated from mock data and a heuristic
model. Always consult a qualified clinician.
