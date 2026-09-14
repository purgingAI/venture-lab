# FocusForge ⚡

**An executive-function OS built specifically for ADHD brains.**

Generic mindfulness / calm apps consistently fail this segment — a proven,
high-demand niche. FocusForge ignores "breathe and relax" and instead attacks
the three needs that actually block ADHD execution:

1. **Task initiation** — the start-wall, not willpower.
2. **Time-blindness** — time made physically *concrete* and visibly shrinking.
3. **Dopamine-aware design** — instant, tactile reward on every win.

### The moat
Every start and every finish is logged into a per-user **behavioral dataset** of
*what actually gets THIS brain to start and finish*. Over time FocusForge knows
your winning strategy and surfaces it first. That personal dataset — not the UI —
is the defensible asset.

### Future vision — "Ambient mode (2036)"
The in-app panel previews the endgame: an always-on exec-function **agent** that
watches context across every device (idle doc, looming deadline, tab-thrash,
restless HRV) and fires a *2-minute start* nudge before avoidance locks in.

---

## Features
- **2-minute starter step** — expand any task into an absurdly small, physical-first
  entry point that beats initiation paralysis. Uses a live **Claude** call
  (`claude-sonnet-4-6`) when an API key is present, with a deterministic **MOCK**
  fallback otherwise.
- **Body-doubling focus session** — animated shrinking ring **and** bar, an
  ambient "people forging alongside you" presence, and time expressed in concrete
  terms to fight time-blindness. Durations: 2 / 10 / 25 min.
- **Dopamine reward burst** — a canvas particle celebration on every completion.
- **"What works for you" intelligence panel** — learns which start-strategies you
  finish most and marks your top one.
- **Ambient-mode preview** — simulate a context-aware nudge.

## Stack
Python 3 **stdlib only** (`http.server`, `sqlite3`, `urllib`). No pip, no apt, no
browser, no external framework. Single-file `index.html`. SQLite is auto-seeded on
first run (`focusforge.db`).

## Run
```bash
./run.sh            # foreground (Ctrl-C to quit)
./run.sh -d         # detached / background
./run.sh status     # is it up?
./run.sh stop       # stop the detached server
```
Then open **http://localhost:8809**

## API
| Method | Path            | Purpose                                            |
|--------|-----------------|----------------------------------------------------|
| GET    | `/`             | single-page app                                    |
| GET    | `/api/health`   | `{ok, ai:"claude"|"mock", port}`                   |
| GET    | `/api/tasks`    | seeded + user tasks                                |
| GET    | `/api/insights` | strategy finish-rates + your top strategy          |
| POST   | `/api/starter`  | `{id}` or `{title,weight}` → 2-minute starter step |
| POST   | `/api/task`     | `{title,weight}` add a task                        |
| POST   | `/api/complete` | `{id,strategy}` mark done + log a win              |
| POST   | `/api/log`      | `{strategy,outcome}` record what worked            |

## AI wiring
On startup the server reads `ANTHROPIC_API_KEY` from `/home/sagar-uniyal/creator-os/.env`
(or the environment). If found, `/api/starter` calls Claude `claude-sonnet-4-6`
over `urllib`. Any failure (network, timeout, parse) transparently degrades to the
mock generator, so the app always works offline.

Port: **8809**.
