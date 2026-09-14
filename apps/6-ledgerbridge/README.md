# LedgerBridge

**Integration glue for small accounting firms.** A typical firm juggles 60–70
disconnected tools — QuickBooks, Xero, banks, payroll, receipt apps,
spreadsheets — with nothing stitching them together. LedgerBridge is the fabric:

> **connect tools → map their accounts/entities → continuously sync + reconcile → alert on drift.**

The moat is a growing **cross-tool mapping dataset**. The endgame is an
**ambient bookkeeping agent** that keeps the books clean before a human ever
opens them — previewed in the *Ambient Mode (2036)* panel.

---

## Stack

Pure **Python 3 standard library** — no pip, no apt, no external services.

- `server.py` — `http.server` on **port 8806**, serves a single-file `index.html`
- `sqlite3` — seeded on first run (`ledgerbridge.db`, created automatically)
- Optional real **Claude** call (`claude-sonnet-4-6` via `urllib`) if
  `/home/sagar-uniyal/creator-os/.env` contains `ANTHROPIC_API_KEY`; otherwise a
  deterministic **MOCK** fallback keeps every feature working offline.

## Run

```bash
./run.sh              # foreground (Ctrl-C to stop)
./run.sh -d           # detached / background
./run.sh status       # is it running?
./run.sh stop         # stop the background server
```

Then open **http://localhost:8806**.

The SQLite database is seeded automatically on first launch. Delete
`ledgerbridge.db` to reset to fresh mock data.

## Seed data

- **~6 connected tools**: QuickBooks Online, Xero, Mercury Bank, Gusto Payroll,
  Dext Receipts, Google Sheets — each with a sync status (synced / syncing /
  drift / stale).
- **Chart-of-accounts mapping table** between systems — some rows **unmapped**,
  some in **conflict**.
- **~15 cross-system transactions** that should reconcile — a few **mismatched**
  (amount deltas) or **missing** on one side.

## Features

1. **Integrations grid** — connected tools with live sync-status dots.
2. **Node-Map (hero)** — animated canvas; tools are nodes, mappings are edges.
   Mapped edges are solid emerald with flowing packets; unmapped/conflict edges
   are dashed red/amber.
3. **Reconcile** (`POST /api/reconcile`) — cross-system mismatches with amounts,
   deltas, and an AI-suggested fix per line plus an AI drift summary.
4. **Mapping resolver** — pick an unmapped/conflict account → AI-suggested target
   account with rationale → **accept** (persists to SQLite, updates the node-map)
   or **reject**.
5. **Sync Intelligence** — accounts mapped %, open drift $, unmapped/conflict
   counts, last sync — plus **Ambient Mode** preview: one autonomous pass that
   auto-maps remaining accounts and clears drift to $0.00.

## API

| Method | Path                   | Purpose |
|--------|------------------------|---------|
| GET    | `/`                    | UI (single-file `index.html`) |
| GET    | `/api/state`           | tools + accounts + txns + intelligence |
| GET    | `/api/health`          | `{ok, ai_mode, port}` |
| POST   | `/api/reconcile`       | cross-system mismatches + AI fixes/summary |
| POST   | `/api/suggest-mapping` | `{account_id}` → AI-suggested target account |
| POST   | `/api/accept-mapping`  | persist an accepted mapping |
| POST   | `/api/reject-mapping`  | reject a suggestion (manual review) |
| POST   | `/api/ambient`         | ambient agent pass: auto-map + clear drift |

## Theme

Futuristic dark data-ops console — **Emerald `#10b981`** on
**Charcoal `#111827`**, glass panels, the node-map as the hero.
