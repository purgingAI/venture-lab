# FreightNormal — Freight Quote Normalization Console

Freight brokers and shippers get carrier quotes as messy free-text emails —
different labels, buried fuel surcharges, scattered accessorials, no per-mile.
**FreightNormal** pastes any messy quote and normalizes it into one clean schema
(`linehaul / fuel / accessorials / total / per-mile`), then ranks quotes head-to-head,
prices them against a compounding lane-rate corpus, and calculates your margin.

The moat is the **normalized lane corpus** — every quote you normalize is persisted,
so the "is this above or below market?" verdict gets sharper the more you use it.

## Stack
- **Python 3 stdlib only** — no pip, no external packages, no browser drivers.
- `server.py`: `http.server` on **port 8804**, serves a single-file `index.html`.
- `sqlite3` (`freightnormal.db`) auto-seeded on first run with ~12 historical
  normalized quotes across 4 lanes.
- Quote parsing uses a **real Claude call** (`claude-sonnet-4-6` via `urllib`)
  when `ANTHROPIC_API_KEY` is found in `/home/sagar-uniyal/creator-os/.env`
  (or the environment); otherwise a deterministic **MOCK** regex/heuristic parser
  runs fully offline. Either way the app works.

## Run
```bash
./run.sh          # foreground
./run.sh -d       # detached (writes .freightnormal.pid + freightnormal.log)
./run.sh status   # running / stopped
./run.sh stop     # stop the detached server
```
Then open http://localhost:8804

## Endpoints
| Method | Path                        | Purpose                                                                 |
|--------|-----------------------------|-------------------------------------------------------------------------|
| GET    | `/`                         | single-file UI (`index.html`)                                           |
| GET    | `/api/health`               | `{ok, engine, model}` — `engine="claude"` when a real key is wired      |
| GET    | `/api/lanes`                | `{lanes:[...]}` — per-lane corpus stats (avg $/mi, min/avg/max total)   |
| POST   | `/api/normalize`            | parse messy quote text → normalized schema + market verdict             |
| GET    | `/api/ambient?origin&dest`  | 2036 ambient-agent auto-book simulation for a lane                      |

### `POST /api/normalize` body
```json
{ "text": "<raw pasted quote>", "save": true }
```
Returns:
```json
{
  "normalized": {
    "origin": "Chicago, IL", "dest": "Dallas, TX", "miles": 925,
    "carrier": "Summit Freightways",
    "linehaul": 1395.0, "fuel": 298.0, "accessorials": 120.0,
    "total": 1813.0, "per_mile": 1.960,
    "accessorial_items": [{"label": "detention", "amount": 120.0}],
    "_engine": "mock-heuristic"
  },
  "market": {
    "market_per_mile": 1.978, "scope": "this lane",
    "verdict": "at market", "delta_pct": -0.9
  }
}
```
Set `"save": false` to normalize without adding the quote to the corpus.

### `GET /api/lanes` → `{lanes:[...]}`
Each lane: `lane, origin, dest, n, miles, avg_per_mile, min_total, avg_total, max_total`.

### `GET /api/ambient?origin=..&dest=..`
Returns `{lane, carriers:[{carrier,total,per_mile}], booked, savings_vs_worst}`.
Defaults to the busiest lane when origin/dest are omitted.

## Frontend features
- **Normalize** — paste any quote (2–3 messy sample presets included) → clean schema.
- **Compare** — collect normalized quotes into a ranked table (best total / best $/mi)
  with a live **margin calculator** (your sell price vs. best cost).
- **Lane Intelligence** — corpus $/mi per lane, and each normalized quote flagged
  above / at / below market.
- **Pricing** — $99/mo base + per-quote overage widget.
- **Ambient mode (2036)** — a glimpse of autonomous lane booking.
