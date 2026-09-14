# Mesa — the company's ambient operational brain

Mesa is an always-on **operational brain for the underserved mid-market**
(100–2,000 employees) — the firms stuck between free SMB tools and $100K+
enterprise platforms. It unifies internal tools into one **operational graph**
(people → projects → work → systems) and lets anyone *"ask the company"* and get
a grounded answer, then act.

**Moat:** the company's own operational graph. Mesa is *born ambient* — it
doesn't wait to be asked; it proactively surfaces the operational signal that
matters and drafts the action.

Part of the **ACE family** — violet/space theme, animated Fibonacci-sphere
"mesh core" globe, "ask the company" console. Dogfoods the ACE look & feel.

## Stack
Python 3 **stdlib only** — no pip, no apt, no browser, nothing to install.
- `server.py` — `http.server` on **port 8810**, serves a single-file `index.html`.
- `sqlite3` — seeded on first run with a mock mid-market company
  (**Northwind Labs**): 12 people, 8 projects, 12 work items, 6 systems.
- **Claude**: if `/home/sagar-uniyal/creator-os/.env` has `ANTHROPIC_API_KEY`,
  the `/api/ask` endpoint makes a real `claude-sonnet-4-6` call grounded in the
  retrieved rows. Otherwise it falls back to a deterministic **mock** answer.
  Either way the answer is grounded and cites its sources.

## Run
```bash
./run.sh          # foreground (Ctrl-C to stop)
./run.sh -d       # background daemon
./run.sh status   # is it up?
./run.sh stop     # stop the daemon
```
Then open http://localhost:8810

The sqlite DB (`mesa.db`) is created and seeded automatically on first request.
Delete it to reseed fresh.

## Features
1. **Ask the company** console — `POST /api/ask` returns a grounded answer plus
   the people / projects / systems it drew from (shown as source chips).
2. **Mesh-core globe** — rotating violet Fibonacci-sphere as the hero.
3. **Operational Graph** panel — teams → projects → work with live status
   (On Track / At Risk / Blocked).
4. **Suggested question chips** — "what's at risk this week?", "who owns the
   Atlas migration?", "where are we blocked?", "who should I ask about SOC 2?".
5. **Ambient mode** — the brain proactively surfaces the top operational alert
   (e.g. *"Atlas Migration slipped — blockers, owner overloaded"*) and drafts
   the action, with a one-click "Ask Mesa to fix it".

## Endpoints
| Method | Path           | Purpose                                             |
|--------|----------------|-----------------------------------------------------|
| GET    | `/`            | Single-file UI (`index.html`)                       |
| GET    | `/api/graph`   | Full operational graph (people/projects/work/systems)|
| GET    | `/api/ambient` | Proactive operational alert + drafted action        |
| GET    | `/api/health`  | `{ok, engine: claude|mock, port}`                   |
| POST   | `/api/ask`     | `{question}` → `{answer, sources, engine}` grounded |
