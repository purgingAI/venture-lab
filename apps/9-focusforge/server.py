#!/usr/bin/env python3
"""
FocusForge — an executive-function OS for ADHD brains.

Stdlib-only backend (http.server) on PORT 8809. Serves a single-file index.html,
a small JSON API backed by sqlite3, and an AI "2-minute starter step" generator
that calls Claude (claude-sonnet-4-6) when ANTHROPIC_API_KEY is available in
/home/sagar-uniyal/creator-os/.env, with a deterministic MOCK fallback otherwise.

Design goals (why this exists): generic mindfulness apps fail ADHD brains. This
targets the real unmet needs — TASK INITIATION, TIME-BLINDNESS, and dopamine-aware
design — and quietly builds a per-user behavioral dataset of "what actually gets
THIS person to start/finish" (the moat).
"""

import json
import os
import random
import re
import sqlite3
import sys
import threading
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8809
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "focusforge.db")
INDEX_PATH = os.path.join(HERE, "index.html")
ENV_PATH = "/home/sagar-uniyal/creator-os/.env"
MODEL = "claude-sonnet-4-6"

_db_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Environment / API key
# --------------------------------------------------------------------------- #
def load_api_key():
    """Read ANTHROPIC_API_KEY from the creator-os .env without extra deps."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


API_KEY = load_api_key()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create schema + seed sample tasks and a 'what worked' log on first run."""
    fresh = not os.path.exists(DB_PATH)
    conn = connect()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            weight TEXT NOT NULL DEFAULT 'medium',   -- how heavy it feels
            status TEXT NOT NULL DEFAULT 'open',      -- open | done
            starter TEXT,                             -- last AI 2-min starter
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS what_worked (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,        -- e.g. "2-minute starter", "body-double"
            task_title TEXT,
            outcome TEXT NOT NULL,         -- started | finished
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT,                     -- what they focused on
            minutes INTEGER NOT NULL,      -- session length in minutes
            finished INTEGER NOT NULL DEFAULT 1,  -- 1 = completed, 0 = bailed
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()

    if fresh:
        sample_tasks = [
            ("Reply to the 3 emails I've been avoiding", "heavy"),
            ("Draft the Q3 project proposal", "heavy"),
            ("Clean and reset my desk", "light"),
            ("File last month's expense receipts", "medium"),
            ("Book the dentist appointment", "light"),
            ("Outline the demo talk", "medium"),
        ]
        cur.executemany(
            "INSERT INTO tasks (title, weight) VALUES (?, ?)", sample_tasks
        )
        seed_log = [
            ("2-minute starter", "Draft the Q3 project proposal", "finished"),
            ("2-minute starter", "Reply to the 3 emails I've been avoiding", "started"),
            ("body-double", "Outline the demo talk", "finished"),
            ("body-double", "Clean and reset my desk", "finished"),
            ("2-minute starter", "File last month's expense receipts", "finished"),
            ("ambient nudge", "Book the dentist appointment", "started"),
            ("body-double", "Draft the Q3 project proposal", "finished"),
            ("2-minute starter", "Outline the demo talk", "finished"),
        ]
        cur.executemany(
            "INSERT INTO what_worked (strategy, task_title, outcome) VALUES (?, ?, ?)",
            seed_log,
        )
        seed_sessions(cur)
        conn.commit()
    conn.close()


def seed_sessions(cur):
    """Seed ~14 days of plausible focus history so the dashboard looks alive.

    Weighted toward mornings/afternoons (higher completion) with a couple of
    lighter/skipped days so the streak + completion-rate numbers feel real.
    """
    rng = random.Random(8809)  # deterministic so the demo is stable
    tasks = [
        "Draft the Q3 project proposal", "Reply to avoided emails",
        "Outline the demo talk", "Clean and reset my desk",
        "File expense receipts", "Deep-work: architecture doc",
        "Review pull requests", "Sketch onboarding flow",
    ]
    # (hour, weight) buckets — mornings/afternoons dominate, some evenings
    hour_pool = [8, 9, 9, 10, 10, 11, 13, 14, 14, 15, 16, 19, 20, 21]
    rows = []
    today = datetime.now()
    for d in range(13, -1, -1):  # 14 days incl. today, oldest first
        day = today - timedelta(days=d)
        # keep the streak intact but vary intensity; day 11 ago is a rest day
        if d in (11,):
            continue  # a genuine off day (breaks longest-streak nicely)
        n_sessions = rng.choice([1, 1, 2, 2, 3])
        for _ in range(n_sessions):
            hour = rng.choice(hour_pool)
            minute = rng.choice([0, 5, 15, 30, 45])
            ts = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            minutes = rng.choice([2, 10, 10, 25, 25, 25])
            # ~82% completion; evenings a bit more likely to bail
            bail_chance = 0.30 if hour >= 19 else 0.12
            finished = 0 if rng.random() < bail_chance else 1
            task = rng.choice(tasks)
            rows.append((task, minutes, finished, ts.strftime("%Y-%m-%d %H:%M:%S")))
    cur.executemany(
        "INSERT INTO sessions (task, minutes, finished, created_at) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


# --------------------------------------------------------------------------- #
# AI: 2-minute starter step (real Claude call w/ mock fallback)
# --------------------------------------------------------------------------- #
def mock_starter(title, weight):
    """Deterministic ADHD-friendly starter step when no API key / call fails."""
    t = (title or "this task").strip().rstrip(".")
    lower = t.lower()
    if "email" in lower:
        step = ("Open your inbox and just re-read ONE of the emails. Don't reply "
                "yet — only decide the single first sentence you'd type.")
    elif "desk" in lower or "clean" in lower:
        step = ("Pick up exactly 3 objects near your keyboard and put them where "
                "they belong. Stop after 3. That's it.")
    elif "proposal" in lower or "draft" in lower or "outline" in lower:
        step = ("Open a blank doc and type only the title plus 3 bullet fragments "
                "— not sentences. Ugly is allowed.")
    elif "receipt" in lower or "expense" in lower or "file" in lower:
        step = ("Open the folder and drag in ONE receipt. Name it anything. "
                "You're just proving the pipe works.")
    elif "book" in lower or "appointment" in lower or "call" in lower:
        step = ("Find the phone number and type it into your dialer — do NOT press "
                "call yet. Just get it staged.")
    else:
        step = (f"Set a 2-minute timer and do the tiniest visible piece of "
                f"'{t}'. The goal is momentum, not completion.")
    return {
        "starter": step,
        "why": ("ADHD task-initiation is a start-cost problem, not a willpower "
                "problem. A 2-minute, low-stakes first move lowers the activation "
                "energy so the brain can slip past the wall."),
        "source": "mock",
    }


def claude_starter(title, weight):
    """Ask Claude for a 2-minute starter step. Returns dict; raises on failure."""
    prompt = (
        "You are FocusForge, an executive-function coach for ADHD brains. "
        "The user is stuck on this task and cannot start it.\n\n"
        f"TASK: {title}\n"
        f"HOW HEAVY IT FEELS: {weight}\n\n"
        "Give ONE concrete, tiny 'starter step' that takes about 2 minutes and "
        "beats task-initiation paralysis. It must be physically-first, absurdly "
        "small, and explicitly NOT ask them to finish the task. Then give a one-"
        "sentence reason it helps an ADHD brain.\n\n"
        "Respond ONLY with strict JSON: "
        '{"starter": "<the 2-minute step>", "why": "<one sentence>"}'
    )
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = "".join(
        blk.get("text", "") for blk in payload.get("content", [])
        if blk.get("type") == "text"
    ).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON in model response")
    data = json.loads(match.group(0))
    return {
        "starter": data.get("starter", "").strip(),
        "why": data.get("why", "").strip(),
        "source": "claude",
    }


def generate_starter(title, weight):
    if API_KEY:
        try:
            out = claude_starter(title, weight)
            if out.get("starter"):
                return out
        except Exception as exc:  # noqa: BLE001 — degrade gracefully to mock
            sys.stderr.write(f"[focusforge] Claude call failed, using mock: {exc}\n")
    return mock_starter(title, weight)


# --------------------------------------------------------------------------- #
# Intelligence panel: what works for this user
# --------------------------------------------------------------------------- #
def compute_insights():
    conn = connect()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT strategy,
               SUM(CASE WHEN outcome='finished' THEN 1 ELSE 0 END) AS finished,
               COUNT(*) AS total
        FROM what_worked
        GROUP BY strategy
        ORDER BY finished DESC, total DESC
        """
    ).fetchall()
    conn.close()
    strategies = []
    for r in rows:
        total = r["total"] or 0
        finished = r["finished"] or 0
        rate = round((finished / total) * 100) if total else 0
        strategies.append(
            {
                "strategy": r["strategy"],
                "finished": finished,
                "total": total,
                "finishRate": rate,
            }
        )
    top = strategies[0]["strategy"] if strategies else None
    return {"strategies": strategies, "top": top}


# --------------------------------------------------------------------------- #
# Momentum: streaks + session-history stats (the retention/moat layer)
# --------------------------------------------------------------------------- #
def _bucket_for_hour(hour):
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


def compute_stats():
    """Streaks, totals, completion rate, 14-day minutes, best focus window."""
    conn = connect()
    rows = conn.execute(
        "SELECT minutes, finished, created_at FROM sessions ORDER BY created_at"
    ).fetchall()
    conn.close()

    total_sessions = len(rows)
    finished_sessions = sum(1 for r in rows if r["finished"])
    total_minutes = sum((r["minutes"] or 0) for r in rows if r["finished"])
    completion_rate = (
        round((finished_sessions / total_sessions) * 100) if total_sessions else 0
    )

    # --- days with >=1 finished session (for streaks) ---
    finished_days = set()
    for r in rows:
        if not r["finished"]:
            continue
        try:
            dt = datetime.strptime(r["created_at"][:10], "%Y-%m-%d").date()
            finished_days.add(dt)
        except (ValueError, TypeError):
            pass

    # longest streak across all history
    longest = 0
    if finished_days:
        ordered = sorted(finished_days)
        run = 1
        longest = 1
        for i in range(1, len(ordered)):
            if (ordered[i] - ordered[i - 1]).days == 1:
                run += 1
            else:
                run = 1
            longest = max(longest, run)

    # current streak: consecutive days ending today (or yesterday, grace)
    today = datetime.now().date()
    current = 0
    if today in finished_days or (today - timedelta(days=1)) in finished_days:
        cursor = today if today in finished_days else today - timedelta(days=1)
        while cursor in finished_days:
            current += 1
            cursor -= timedelta(days=1)

    # --- minutes by day for the last 14 days (oldest -> newest) ---
    per_day = {}
    for r in rows:
        if not r["finished"]:
            continue
        try:
            dt = datetime.strptime(r["created_at"][:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        per_day[dt] = per_day.get(dt, 0) + (r["minutes"] or 0)
    minutes_by_day = []
    for d in range(13, -1, -1):
        day = today - timedelta(days=d)
        minutes_by_day.append(
            {"date": day.isoformat(), "minutes": per_day.get(day, 0)}
        )

    # --- best time-of-day bucket by completion rate ---
    buckets = {
        "morning": {"finished": 0, "total": 0},
        "afternoon": {"finished": 0, "total": 0},
        "evening": {"finished": 0, "total": 0},
    }
    for r in rows:
        try:
            dt = datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        b = buckets[_bucket_for_hour(dt.hour)]
        b["total"] += 1
        if r["finished"]:
            b["finished"] += 1
    bucket_stats = []
    for name, v in buckets.items():
        rate = round((v["finished"] / v["total"]) * 100) if v["total"] else 0
        bucket_stats.append(
            {"bucket": name, "finished": v["finished"],
             "total": v["total"], "rate": rate}
        )
    # best = highest completion rate, tie-broken by volume; require >=1 session
    scored = [b for b in bucket_stats if b["total"] > 0]
    best_window = None
    if scored:
        best = max(scored, key=lambda b: (b["rate"], b["total"]))
        best_window = best["bucket"]

    return {
        "currentStreak": current,
        "longestStreak": longest,
        "totalMinutes": total_minutes,
        "totalSessions": total_sessions,
        "finishedSessions": finished_sessions,
        "completionRate": completion_rate,
        "minutesByDay": minutes_by_day,
        "buckets": bucket_stats,
        "bestWindow": best_window,
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "FocusForge/1.0"

    def log_message(self, fmt, *args):  # quiet logs, minimal footprint
        pass

    # -- helpers ------------------------------------------------------------ #
    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- GET ---------------------------------------------------------------- #
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as fh:
                    data = fh.read()
            except OSError:
                self.send_error(404, "index.html missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path == "/api/health":
            self._send_json({"ok": True, "ai": "claude" if API_KEY else "mock",
                             "port": PORT})
            return

        if self.path == "/api/tasks":
            with _db_lock:
                conn = connect()
                rows = conn.execute(
                    "SELECT id, title, weight, status, starter FROM tasks "
                    "ORDER BY status='done', id"
                ).fetchall()
                conn.close()
            self._send_json({"tasks": [dict(r) for r in rows]})
            return

        if self.path == "/api/insights":
            with _db_lock:
                self._send_json(compute_insights())
            return

        if self.path == "/api/stats":
            with _db_lock:
                self._send_json(compute_stats())
            return

        self.send_error(404, "not found")

    # -- POST --------------------------------------------------------------- #
    def do_POST(self):
        if self.path == "/api/starter":
            body = self._read_json()
            task_id = body.get("id")
            title = (body.get("title") or "").strip()
            weight = (body.get("weight") or "medium").strip()
            if task_id and not title:
                with _db_lock:
                    conn = connect()
                    row = conn.execute(
                        "SELECT title, weight FROM tasks WHERE id=?", (task_id,)
                    ).fetchone()
                    conn.close()
                if row:
                    title, weight = row["title"], row["weight"]
            if not title:
                self._send_json({"error": "title required"}, 400)
                return
            result = generate_starter(title, weight)
            if task_id:
                with _db_lock:
                    conn = connect()
                    conn.execute(
                        "UPDATE tasks SET starter=? WHERE id=?",
                        (result["starter"], task_id),
                    )
                    conn.commit()
                    conn.close()
            self._send_json(result)
            return

        if self.path == "/api/log":
            body = self._read_json()
            strategy = (body.get("strategy") or "").strip()
            outcome = (body.get("outcome") or "started").strip()
            task_title = (body.get("task_title") or "").strip() or None
            if not strategy:
                self._send_json({"error": "strategy required"}, 400)
                return
            with _db_lock:
                conn = connect()
                conn.execute(
                    "INSERT INTO what_worked (strategy, task_title, outcome) "
                    "VALUES (?, ?, ?)",
                    (strategy, task_title, outcome),
                )
                conn.commit()
                conn.close()
                insights = compute_insights()
            self._send_json({"ok": True, "insights": insights})
            return

        if self.path == "/api/complete":
            body = self._read_json()
            task_id = body.get("id")
            if not task_id:
                self._send_json({"error": "id required"}, 400)
                return
            with _db_lock:
                conn = connect()
                row = conn.execute(
                    "SELECT title FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                conn.execute(
                    "UPDATE tasks SET status='done' WHERE id=?", (task_id,)
                )
                if row:
                    conn.execute(
                        "INSERT INTO what_worked (strategy, task_title, outcome) "
                        "VALUES (?, ?, ?)",
                        (body.get("strategy") or "body-double", row["title"],
                         "finished"),
                    )
                conn.commit()
                conn.close()
                insights = compute_insights()
            self._send_json({"ok": True, "insights": insights})
            return

        if self.path == "/api/session":
            body = self._read_json()
            task = (body.get("task") or "").strip() or None
            try:
                minutes = int(body.get("minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0
            if minutes <= 0:
                self._send_json({"error": "minutes required"}, 400)
                return
            finished = 1 if body.get("finished", True) else 0
            with _db_lock:
                conn = connect()
                conn.execute(
                    "INSERT INTO sessions (task, minutes, finished) "
                    "VALUES (?, ?, ?)",
                    (task, minutes, finished),
                )
                conn.commit()
                conn.close()
                stats = compute_stats()
            self._send_json({"ok": True, "stats": stats})
            return

        if self.path == "/api/task":
            body = self._read_json()
            title = (body.get("title") or "").strip()
            weight = (body.get("weight") or "medium").strip()
            if not title:
                self._send_json({"error": "title required"}, 400)
                return
            with _db_lock:
                conn = connect()
                cur = conn.execute(
                    "INSERT INTO tasks (title, weight) VALUES (?, ?)",
                    (title, weight),
                )
                new_id = cur.lastrowid
                conn.commit()
                conn.close()
            self._send_json({"ok": True, "id": new_id})
            return

        self.send_error(404, "not found")


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ai = "Claude (claude-sonnet-4-6)" if API_KEY else "MOCK (no API key)"
    sys.stdout.write(
        f"FocusForge running on http://localhost:{PORT}  |  AI: {ai}\n"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
