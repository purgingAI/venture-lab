# CloseLoop — Real-Estate Transaction Reconciliation Console

Reconcile a real-estate closing's **expected vs actual** money movements before it
funds. CloseLoop ingests the money movements + closing-statement line items for
each transaction, reconciles them, and surfaces **exceptions** (missing earnest
deposit, commission mismatch, fee/credit discrepancy) with a plain-English AI
explanation and recommended fix — the kind of drift that blows up small-brokerage
closings.

> Market gap scored **8.5/10**. Moat: a cross-brokerage transaction-state dataset.
> Future: an autonomous closing coordinator (see **Ambient mode · 2036**).

## Stack
- **Python 3 stdlib only** — no pip, no apt, nothing to install.
- `http.server` backend (`server.py`) on **PORT 8805**, serves a single-file `index.html`.
- `sqlite3` seeded on first run with ~6 mock transactions (a few with deliberate discrepancies).
- **Claude** (`claude-sonnet-4-6` via `urllib`) explains each exception if
  `~/creator-os/.env` has `ANTHROPIC_API_KEY`; otherwise a built-in **MOCK** analyst.

## Run
```bash
./run.sh          # foreground
./run.sh -d       # background daemon
./run.sh status   # is it up?
./run.sh stop     # stop the daemon
```
Then open **http://localhost:8805**. The sqlite db (`closeloop.db`) is created & seeded on first run.

## Features
1. **Transactions list** — reconciliation status (Balanced / Exceptions / Cleared) + `$ out of balance`.
2. **Expected-vs-actual ledger** — two-column diff, mismatched rows highlighted red/green, net variance.
3. **POST `/api/reconcile`** — exceptions with severity + AI explanation/fix.
4. **Close Timeline** — earnest → inspection → appraisal → clear-to-close → funded, each with its money state.
5. **Portfolio Intelligence** — avg time-to-close, most common exception type + **Ambient-mode** preview (agent auto-chases a missing earnest deposit).

## API
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | status + AI mode (live/mock) |
| GET | `/api/transactions` | list w/ status + out-of-balance |
| GET | `/api/transaction/<id>` | ledger + milestones/money-state |
| GET | `/api/portfolio` | portfolio intelligence KPIs |
| POST | `/api/reconcile` | `{ "txn_id": "TX-4022" }` → exceptions + AI |

UI: dark futuristic escrow console, **slate `#64748b` + gold `#eab308`**, glass panels,
horizontal closing timeline as the hero, crisp red/green two-column ledger diff.
