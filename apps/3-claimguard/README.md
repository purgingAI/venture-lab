# ClaimGuard — Denial Radar

Pre-submission medical billing-error catcher for **small medical practices**.
Upload/paste a claim (CPT / ICD / payer / amount) → an AI + proprietary
**denial-pattern ruleset** flags likely denials and coding errors *before*
submission, with estimated recovered revenue and a plain-English fix per flag.

The moat is the **denial-pattern dataset** that compounds with every claim scanned.
Pricing is **value-based**: a % of the denials we prevent.

## Stack
- **Python 3 stdlib only** — no pip, no external packages, no browser drivers.
- `server.py`: `http.server` on **port 8803**, serves a single-file `index.html`.
- `sqlite3` (`claimguard.db`) auto-seeded on first run: ~15 mock claims + a
  `denial_rules` table (missing modifier, ICD/CPT mismatch, payer caps,
  stale authorization, duplicate, telehealth POS).
- Flag explanations use a **real Claude call** (`claude-sonnet-4-6` via `urllib`)
  when `ANTHROPIC_API_KEY` is found in `/home/sagar-uniyal/creator-os/.env`
  (or the env), otherwise a deterministic **MOCK** fallback. Either way the app works.

## Run
```bash
./run.sh          # foreground
./run.sh -d       # detached (writes .claimguard.pid + claimguard.log)
./run.sh status   # running / stopped
./run.sh stop     # stop the detached server
```
Then open http://localhost:8803

## Endpoints
| Method | Path              | Purpose                                                        |
|--------|-------------------|----------------------------------------------------------------|
| GET    | `/`               | single-file UI (`index.html`)                                  |
| GET    | `/api/health`     | `{ok, ai}` — `ai=true` when a real key is wired               |
| GET    | `/api/dashboard`  | all claims + stats (protected / exposure) + learned rules      |
| POST   | `/api/scan`       | score a claim → risk, flags, $ at risk, per-flag fix           |
| POST   | `/api/fix`        | mark a claim corrected → adds to "revenue protected"           |
| GET    | `/api/ambient`    | 2036 ambient-agent auto-appeal simulation steps                |

### `POST /api/scan` body
```json
{"patient":"Reyes, D.","payer":"Aetna","cpt":"97110","icd":"M54.5",
 "amount":680,"modifier":"","auth_status":"active","pos":"11","telehealth":0}
```
Pass `{"id": <n>}` instead to rescan an existing seeded claim.

## Features in the UI
1. **Claim intake → scan** with a scanner animation + risk gauge, rule-hit flags,
   $ at risk and a plain-English fix each.
2. **Claim ledger** dashboard — Clean / At-Risk / Fixed with a running
   *revenue protected* counter and open-exposure total.
3. **Denial-Pattern Intelligence** — top learned denial reasons (compounding moat).
4. **Value-Based Pricing** widget — computes monthly denials prevented and your % fee.
5. **Ambient Mode (2036)** — simulates an agent that auto-corrects and auto-appeals
   a denied claim with zero human touch.

## Notes
- To reseed, stop the server and delete `claimguard.db`.
- Nothing is left running by this repo; use `./run.sh stop` for the detached mode.
