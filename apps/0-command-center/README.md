# Command Center

A live operations command center that unifies all **10 Venture Lab apps** into a
single futuristic fleet dashboard. Server-side it polls every app concurrently
(1s timeout each), pulls one real headline metric from each, and renders a
10-tile grid with up/down pulse dots, a portfolio KPI header, a starfield, and
an animated mesh — the ACE-family aesthetic (near-black space, violet accents,
glassmorphism). Auto-refreshes every 5s.

## Stack
Python 3 **stdlib only** (`http.server` + `urllib` + `json`). No installs, no
browsers, no external dependencies. Single-file front end (`index.html`).

## Run
```bash
./run.sh          # foreground  (Ctrl-C to quit)
./run.sh -d       # detached / background
./run.sh status   # is it running?
./run.sh stop     # stop the detached server
```
Then open **http://localhost:8811**

## Endpoints
| Endpoint       | Description                                                                 |
|----------------|-----------------------------------------------------------------------------|
| `GET /`        | Single-file command-center UI.                                              |
| `GET /api/fleet` | Concurrently polls all 10 apps, returns 10 tiles + status + a headline metric each. Down apps are marked offline (`○`) — never crashes. |
| `GET /api/health` | Liveness check for the Command Center itself.                            |

## The fleet
The metric for each app is read from that app's **real** endpoint/field
(verified against each `server.py`):

| Port | App           | Endpoint         | Headline metric              |
|------|---------------|------------------|------------------------------|
| 8801 | TradePilot    | `/api/state`     | collected $ · pipeline jobs  |
| 8802 | EmberHealth   | `/api/state`     | flare risk /100 · band       |
| 8803 | ClaimGuard    | `/api/dashboard` | revenue protected · at-risk  |
| 8804 | FreightNormal | `/api/lanes`     | lanes tracked                |
| 8805 | CloseLoop     | `/api/portfolio` | clean close · out-of-balance |
| 8806 | LedgerBridge  | `/api/state`     | mapped % · open drift        |
| 8807 | LoreKeeper    | `/api/revenue`   | net payout · orders          |
| 8808 | CompliAgent   | `/api/bootstrap` | compliance score /100        |
| 8809 | FocusForge    | `/api/stats`     | current streak · minutes     |
| 8810 | Mesa          | `/api/alerts`    | open alerts · high-severity  |

If an app is down or unreachable it appears as an offline (`○`) tile — the
dashboard still renders all 10 tiles and never errors out.

## Notes
- Port **8811** (slot `0` — sits in front of apps 1–10).
- Each poll has a hard **1s timeout**; all 10 polls run in parallel on threads,
  so a full fleet refresh stays fast even when several apps are down.
