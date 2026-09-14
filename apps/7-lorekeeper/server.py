#!/usr/bin/env python3
"""
LoreKeeper - Creator Super-Fan D2C Engine
Python 3 stdlib only. http.server on PORT 8807.
Serves single-file index.html, JSON API, sqlite fan-graph.

Optional integrations (auto-detected from ~/creator-os/.env):
  ANTHROPIC_API_KEY  -> real Claude persona replies (claude-sonnet-4-6)
  ELEVENLABS_API_KEY -> /api/tts voices the persona reply
Both degrade gracefully to mock / browser speechSynthesis.
"""
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8807
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "lorekeeper.db")
INDEX_PATH = os.path.join(HERE, "index.html")
ENV_PATH = os.path.expanduser("~/creator-os/.env")

CLAUDE_MODEL = "claude-sonnet-4-6"
PLATFORM_FEE = 0.05  # 5% platform fee

# ---------------------------------------------------------------------------
# Env / secrets
# ---------------------------------------------------------------------------

def load_env():
    keys = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    return keys

ENV = load_env()
ANTHROPIC_API_KEY = ENV.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = ENV.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")

# Persona Autopilot on/off flag (held in-memory; the UI toggle drives the loop)
AUTOPILOT_RUNNING = {"on": False}

# Default creator persona (editable from UI, held in-memory)
PERSONA = {
    "creator": "Nova Lumen",
    "voice": "warm, playful, a little cosmic; talks to fans like close friends; "
             "uses vivid imagery, never corporate, occasionally teases the next drop",
    "tagline": "your favorite interdimensional pop-artist",
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

FIRST = ["Aria", "Kai", "Luna", "Ezra", "Mira", "Rhys", "Sena", "Theo",
         "Iris", "Juno", "Dax", "Wren", "Cleo", "Milo", "Nyx", "Zola",
         "Orin", "Vera", "Pax", "Lux", "Remy", "Sol"]
LAST = ["Voss", "Okafor", "Rhee", "Marlowe", "Sato", "Delacroix", "Nash",
        "Bishop", "Hale", "Frost", "Quill", "Vane", "Ash", "Cruz", "Wilde"]
TIERS = ["Nova", "Aurora", "Comet", "Ember"]  # Nova = highest

def seed(conn):
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE fans(
        id INTEGER PRIMARY KEY,
        name TEXT, tier TEXT, spend REAL,
        last_days INTEGER, affinity REAL, wtp REAL, messages INTEGER
    );
    CREATE TABLE drops(
        id INTEGER PRIMARY KEY,
        title TEXT, price REAL, tier_min TEXT, created TEXT, sold INTEGER
    );
    CREATE TABLE persona(
        id INTEGER PRIMARY KEY, creator TEXT, voice TEXT, tagline TEXT
    );
    """)
    rng = random.Random(810)
    used = set()
    for i in range(20):
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in used:
                used.add(name)
                break
        tier = rng.choices(TIERS, weights=[2, 4, 6, 8])[0]
        base = {"Nova": 4200, "Aurora": 1800, "Comet": 620, "Ember": 140}[tier]
        spend = round(base * rng.uniform(0.6, 1.6), 2)
        affinity = round(rng.uniform(0.35, 0.99), 2)
        last_days = rng.choice([0, 1, 2, 3, 5, 8, 14, 21, 33, 47])
        wtp = round(spend / max(6, rng.randint(6, 18)), 2)  # willingness-to-pay per drop
        msgs = rng.randint(2, 240)
        c.execute("INSERT INTO fans(name,tier,spend,last_days,affinity,wtp,messages)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (name, tier, spend, last_days, affinity, wtp, msgs))
    for d in [
        ("Starlight Vinyl (signed)", 89.0, "Comet", 42),
        ("Backstage Hologram Pass", 250.0, "Aurora", 11),
        ("Lost Verses NFT Zine", 35.0, "Ember", 130),
    ]:
        c.execute("INSERT INTO drops(title,price,tier_min,created,sold)"
                  " VALUES(?,?,?,datetime('now'),?)", d)
    c.execute("INSERT INTO persona(id,creator,voice,tagline) VALUES(1,?,?,?)",
              (PERSONA["creator"], PERSONA["voice"], PERSONA["tagline"]))
    conn.commit()

def migrate(conn):
    """Additive migrations — safe to run every boot, never drops anything."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(purchases)").fetchall()}
    if not cols:
        conn.execute("""
        CREATE TABLE purchases(
            id INTEGER PRIMARY KEY,
            fan_id INTEGER, drop_id INTEGER,
            gross REAL, platform_fee REAL, net REAL,
            created TEXT
        )""")
        conn.commit()
        # only seed history if the table was just created
        if conn.execute("SELECT COUNT(*) c FROM purchases").fetchone()["c"] == 0:
            seed_purchases(conn)
    # Persona Autopilot outreach log — additive, never drops anything
    ap = {r["name"] for r in conn.execute(
        "PRAGMA table_info(autopilot_log)").fetchall()}
    if not ap:
        conn.execute("""
        CREATE TABLE autopilot_log(
            id INTEGER PRIMARY KEY,
            ts TEXT,
            fan_id INTEGER,
            fan TEXT,
            message TEXT,
            predicted_lift REAL,
            source TEXT
        )""")
        conn.commit()


def seed_purchases(conn):
    """Seed a handful of historical purchases so /api/revenue looks alive."""
    fans = [dict(r) for r in conn.execute("SELECT * FROM fans").fetchall()]
    drops = [dict(r) for r in conn.execute("SELECT * FROM drops").fetchall()]
    if not fans or not drops:
        return
    order = {t: i for i, t in enumerate(TIERS)}
    rng = random.Random(8107)
    rows = []
    # spread ~40 historical buys across the last 30 days, weighted to loyal fans
    for _ in range(40):
        drop = rng.choice(drops)
        cutoff = order.get(drop["tier_min"], len(TIERS) - 1)
        eligible = [f for f in fans if order.get(f["tier"], 9) <= cutoff]
        if not eligible:
            eligible = fans
        # weight buyer selection by affinity so top fans dominate spend
        fan = rng.choices(eligible, weights=[0.2 + f["affinity"] for f in eligible])[0]
        days_ago = rng.randint(0, 29)
        gross = round(float(drop["price"]), 2)
        fee = round(gross * PLATFORM_FEE, 2)
        net = round(gross - fee, 2)
        rows.append((fan["id"], drop["id"], gross, fee, net, days_ago))
    for fan_id, drop_id, gross, fee, net, days_ago in rows:
        conn.execute(
            "INSERT INTO purchases(fan_id,drop_id,gross,platform_fee,net,created)"
            " VALUES(?,?,?,?,?,datetime('now',?))",
            (fan_id, drop_id, gross, fee, net, f"-{days_ago} days"))
        conn.execute("UPDATE drops SET sold=sold+1 WHERE id=?", (drop_id,))
        conn.execute("UPDATE fans SET spend=spend+? WHERE id=?", (gross, fan_id))
    conn.commit()


def get_conn():
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if fresh:
        seed(conn)
    migrate(conn)
    return conn

def load_persona(conn):
    r = conn.execute("SELECT creator,voice,tagline FROM persona WHERE id=1").fetchone()
    if r:
        return {"creator": r["creator"], "voice": r["voice"], "tagline": r["tagline"]}
    return dict(PERSONA)

# ---------------------------------------------------------------------------
# Claude persona reply
# ---------------------------------------------------------------------------

def claude_reply(persona, fan, fan_message):
    """Call Claude for an in-voice reply, else return a mock. Returns (text, source)."""
    tier = fan["tier"] if fan else "Comet"
    fname = fan["name"] if fan else "friend"
    system = (
        f"You ARE {persona['creator']}, {persona['tagline']}. "
        f"You are replying directly to a superfan named {fname} "
        f"(loyalty tier: {tier}). Speak in first person, in THIS voice: "
        f"{persona['voice']}. Keep it 2-4 sentences, intimate and specific, "
        f"never say you are an AI, never use markdown. Make {fname} feel seen. "
        f"You may lightly tease an upcoming exclusive drop."
    )
    if not ANTHROPIC_API_KEY:
        return (_mock_reply(persona, fname, tier, fan_message), "mock")
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 300,
        "system": system,
        "messages": [{"role": "user", "content": fan_message}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = "".join(
            b.get("text", "") for b in data.get("content", [])
            if b.get("type") == "text").strip()
        if text:
            return (text, "claude")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        pass
    return (_mock_reply(persona, fname, tier, fan_message), "mock")

def _mock_reply(persona, fname, tier, msg):
    openers = [
        f"{fname}, hey — I felt that message land like a comet tail. ",
        f"Oh {fname}, you always find me at the right frequency. ",
        f"{fname}! Of all the signals tonight, yours glows brightest. ",
    ]
    bodies = [
        "What you said stays with me between the shows.",
        "You've been in this orbit since the early static, and I don't forget that.",
        "The next thing I'm making has a little of you woven into it.",
    ]
    teases = [
        " Stay close — a Nova-tier drop is warming up. ✦",
        " Keep your notifications on; something starlit is coming. ✦",
        " I'm saving one of the first copies with your name on it. ✦",
    ]
    rng = random.Random(hash((fname, msg)) & 0xffffffff)
    return rng.choice(openers) + rng.choice(bodies) + rng.choice(teases)

# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

def eleven_tts(text):
    """Return (audio_bytes, content_type) or (None, None)."""
    if not ELEVENLABS_API_KEY:
        return (None, None)
    voice_id = "EXAVITQu4vr4xnSDxMaL"  # default library voice "Sarah"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({
        "text": text[:900],
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "content-type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY,
        "accept": "audio/mpeg",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return (resp.read(), "audio/mpeg")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return (None, None)

# ---------------------------------------------------------------------------
# Intelligence helpers
# ---------------------------------------------------------------------------

def fan_rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM fans ORDER BY spend DESC").fetchall()]

def intelligence(conn):
    fans = fan_rows(conn)
    top = sorted(fans, key=lambda f: f["spend"], reverse=True)[:5]
    # at-risk: cold (>=14 days) OR (high affinity that dipped) — proxy by last_days
    at_risk = sorted([f for f in fans if f["last_days"] >= 14],
                     key=lambda f: (-f["spend"], -f["last_days"]))[:5]
    bands = {"High (>$120)": 0, "Mid ($40-120)": 0, "Low (<$40)": 0}
    for f in fans:
        if f["wtp"] > 120:
            bands["High (>$120)"] += 1
        elif f["wtp"] >= 40:
            bands["Mid ($40-120)"] += 1
        else:
            bands["Low (<$40)"] += 1
    total_spend = round(sum(f["spend"] for f in fans), 2)
    return {
        "top": top, "at_risk": at_risk, "bands": bands,
        "total_spend": total_spend, "fan_count": len(fans),
        "avg_affinity": round(sum(f["affinity"] for f in fans) / len(fans), 2),
    }

def project_drop(conn, price, tier_min):
    """Projected revenue: fans at/above tier_min whose wtp>=price, weighted by affinity."""
    order = {t: i for i, t in enumerate(TIERS)}  # Nova=0 highest
    cutoff = order.get(tier_min, len(TIERS) - 1)
    fans = fan_rows(conn)
    eligible = [f for f in fans if order.get(f["tier"], 9) <= cutoff]
    buyers = [f for f in eligible if f["wtp"] >= price * 0.8]
    # convert probability scaled by affinity
    est_units = sum(1 for f in buyers if random.Random(
        hash((f["id"], price)) & 0xffffffff).random() < (0.4 + 0.55 * f["affinity"]))
    est_units = max(est_units, 1 if buyers else 0)
    gross = round(est_units * price, 2)
    fee = round(gross * PLATFORM_FEE, 2)
    net = round(gross - fee, 2)
    return {
        "eligible": len(eligible), "likely_buyers": len(buyers),
        "est_units": est_units, "gross": gross,
        "platform_fee": fee, "net": net, "fee_pct": PLATFORM_FEE,
    }

def do_purchase(conn, fan_id, drop_id):
    """Record a superfan buying a drop; apply platform fee; bump spend + affinity."""
    fr = conn.execute("SELECT * FROM fans WHERE id=?", (fan_id,)).fetchone()
    dr = conn.execute("SELECT * FROM drops WHERE id=?", (drop_id,)).fetchone()
    if not fr or not dr:
        return {"ok": False, "reason": "not_found"}
    drop = dict(dr)
    gross = round(float(drop["price"]), 2)
    fee = round(gross * PLATFORM_FEE, 2)
    net = round(gross - fee, 2)
    conn.execute(
        "INSERT INTO purchases(fan_id,drop_id,gross,platform_fee,net,created)"
        " VALUES(?,?,?,?,?,datetime('now'))",
        (fan_id, drop_id, gross, fee, net))
    conn.execute("UPDATE drops SET sold=sold+1 WHERE id=?", (drop_id,))
    # bump lifetime spend, refresh recency, and lift affinity (capped at 0.99)
    conn.execute(
        "UPDATE fans SET spend=spend+?, last_days=0, "
        "affinity=MIN(0.99, affinity+0.03) WHERE id=?", (gross, fan_id))
    conn.commit()
    fan = dict(conn.execute("SELECT * FROM fans WHERE id=?", (fan_id,)).fetchone())
    return {
        "ok": True,
        "fan": fan,
        "drop": {"id": drop["id"], "title": drop["title"], "price": drop["price"]},
        "gross": gross,
        "platform_fee": fee,
        "fee_pct": PLATFORM_FEE,
        "creator_payout": net,
    }


def revenue(conn):
    """Creator revenue dashboard: totals, by-drop, top buyers, 30-day trend."""
    purchases = [dict(r) for r in conn.execute("SELECT * FROM purchases").fetchall()]
    gross = round(sum(p["gross"] for p in purchases), 2)
    fee = round(sum(p["platform_fee"] for p in purchases), 2)
    net = round(sum(p["net"] for p in purchases), 2)

    by_drop = [dict(r) for r in conn.execute("""
        SELECT d.id AS drop_id, d.title AS title, d.price AS price,
               COUNT(p.id) AS units,
               COALESCE(SUM(p.gross),0) AS gross,
               COALESCE(SUM(p.net),0) AS net
        FROM drops d LEFT JOIN purchases p ON p.drop_id=d.id
        GROUP BY d.id ORDER BY gross DESC""").fetchall()]
    for r in by_drop:
        r["gross"] = round(r["gross"], 2)
        r["net"] = round(r["net"], 2)

    top_buyers = [dict(r) for r in conn.execute("""
        SELECT f.id AS fan_id, f.name AS name, f.tier AS tier,
               COUNT(p.id) AS orders,
               COALESCE(SUM(p.gross),0) AS spent
        FROM fans f JOIN purchases p ON p.fan_id=f.id
        GROUP BY f.id ORDER BY spent DESC LIMIT 6""").fetchall()]
    for r in top_buyers:
        r["spent"] = round(r["spent"], 2)

    # 30-day trend (fill gaps with 0 so the chart is continuous)
    rows = conn.execute("""
        SELECT date(created) AS d, SUM(gross) AS g
        FROM purchases WHERE created >= datetime('now','-29 days')
        GROUP BY date(created)""").fetchall()
    by_day = {r["d"]: round(r["g"], 2) for r in rows}
    trend = []
    for i in range(29, -1, -1):
        day = conn.execute("SELECT date('now', ?) d", (f"-{i} days",)).fetchone()["d"]
        trend.append({"date": day, "gross": by_day.get(day, 0.0)})

    return {
        "gross": gross, "platform_fee": fee, "net": net,
        "fee_pct": PLATFORM_FEE, "order_count": len(purchases),
        "by_drop": by_drop, "top_buyers": top_buyers, "trend": trend,
    }


def ambient_sim(conn):
    """Persona autonomously re-engages the most valuable at-risk fan."""
    intel = intelligence(conn)
    if not intel["at_risk"]:
        return {"target": None, "steps": [], "message": None}
    target = intel["at_risk"][0]
    persona = load_persona(conn)
    msg = f"(re-engagement) I noticed you've been quiet for {target['last_days']} days."
    reply, source = claude_reply(persona, target,
        f"Write a warm, unprompted check-in to {target['name']} who hasn't "
        f"interacted in {target['last_days']} days. Rekindle the bond and hint "
        f"at something exclusive just for them.")
    steps = [
        f"Scanned fan graph → flagged {target['name']} ({target['tier']}) "
        f"cold for {target['last_days']}d",
        f"Predicted churn risk from affinity {target['affinity']} + spend "
        f"${target['spend']:.0f}",
        "Composed personalized re-engagement in creator voice",
        "Queued a Nova-tier early-access offer as retention hook",
        "Scheduled ambient send + will learn from response",
    ]
    return {"target": target, "steps": steps, "message": reply, "source": source}

# ---------------------------------------------------------------------------
# Persona Autopilot — autonomous re-engagement of cold, high-value superfans
# ---------------------------------------------------------------------------

# minimum days since last contact before autopilot will re-touch a fan
AUTOPILOT_COOLDOWN_DAYS = 7
# how cold a fan must be (days since last seen) to be a re-engagement candidate
AUTOPILOT_RISK_DAYS = 8


def _risk_score(fan):
    """Coldest + highest-value = most at-risk. Higher score = more urgent."""
    return fan["last_days"] * (fan["spend"] + 50 * fan["affinity"])


def autopilot_candidates(conn):
    """Cold, high-value fans not contacted within the cooldown window."""
    fans = fan_rows(conn)
    # fans autopilot has already touched recently (last_days reset to 0 on contact,
    # so we exclude anyone below the risk threshold — they're warm again)
    pool = [f for f in fans if f["last_days"] >= AUTOPILOT_RISK_DAYS]
    return sorted(pool, key=_risk_score, reverse=True)


def _predicted_lift(fan):
    """Estimate re-engagement lift %: colder + higher affinity fans respond more."""
    recency = min(1.0, fan["last_days"] / 45.0)
    lift = (0.18 + 0.42 * fan["affinity"] + 0.25 * recency) * 100.0
    # deterministic jitter so repeated views are stable
    jit = (random.Random(hash((fan["id"], fan["last_days"])) & 0xffffffff)
           .uniform(-4, 4))
    return round(max(3.0, min(85.0, lift + jit)), 1)


def autopilot_step(conn):
    """Run one autonomous outreach cycle. Returns the outreach record (or idle)."""
    cands = autopilot_candidates(conn)
    if not cands:
        return {"ok": True, "idle": True, "reason": "no_at_risk",
                "at_risk_remaining": 0}
    target = cands[0]
    persona = load_persona(conn)
    prompt = (
        f"Write a warm, unprompted re-engagement note to {target['name']}, a "
        f"{target['tier']}-tier superfan who has been quiet for "
        f"{target['last_days']} days (lifetime spend ${target['spend']:.0f}, "
        f"affinity {target['affinity']}). Reference the bond you share, make them "
        f"feel remembered, and gently pull them back with a hint of something "
        f"exclusive. Do not sound like marketing."
    )
    message, source = claude_reply(persona, target, prompt)
    lift = _predicted_lift(target)

    # "send" (simulated): refresh recency + a small affinity nudge (capped)
    conn.execute(
        "UPDATE fans SET last_days=0, affinity=MIN(0.99, affinity+0.02) "
        "WHERE id=?", (target["id"],))
    conn.execute(
        "INSERT INTO autopilot_log(ts,fan_id,fan,message,predicted_lift,source) "
        "VALUES(datetime('now'),?,?,?,?,?)",
        (target["id"], target["name"], message, lift, source))
    conn.commit()

    remaining = max(0, len(cands) - 1)
    return {
        "ok": True, "idle": False,
        "fan_id": target["id"], "fan": target["name"], "tier": target["tier"],
        "last_days": target["last_days"], "spend": round(target["spend"], 2),
        "affinity": target["affinity"],
        "message": message, "predicted_lift": lift, "source": source,
        "at_risk_remaining": remaining,
    }


def autopilot_log(conn, limit=20):
    rows = conn.execute(
        "SELECT ts,fan_id,fan,message,predicted_lift,source FROM autopilot_log "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def autopilot_state(conn):
    contacted = conn.execute(
        "SELECT COUNT(*) c FROM autopilot_log").fetchone()["c"]
    return {
        "contacted_count": contacted,
        "at_risk_remaining": len(autopilot_candidates(conn)),
    }

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        conn = get_conn()
        try:
            if path == "/api/state":
                self._json({
                    "persona": load_persona(conn),
                    "fans": fan_rows(conn),
                    "drops": [dict(r) for r in conn.execute(
                        "SELECT * FROM drops ORDER BY id DESC").fetchall()],
                    "intel": intelligence(conn),
                    "integrations": {
                        "claude": bool(ANTHROPIC_API_KEY),
                        "elevenlabs": bool(ELEVENLABS_API_KEY),
                    },
                })
            elif path == "/api/ambient":
                self._json(ambient_sim(conn))
            elif path == "/api/revenue":
                self._json(revenue(conn))
            elif path == "/api/autopilot/log":
                self._json({"log": autopilot_log(conn)})
            elif path == "/api/autopilot/state":
                st = autopilot_state(conn)
                st["running"] = AUTOPILOT_RUNNING["on"]
                self._json(st)
            else:
                self.send_error(404)
        finally:
            conn.close()

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()
        conn = get_conn()
        try:
            if path == "/api/chat":
                fan = None
                fid = body.get("fan_id")
                if fid is not None:
                    r = conn.execute("SELECT * FROM fans WHERE id=?", (fid,)).fetchone()
                    fan = dict(r) if r else None
                persona = load_persona(conn)
                text, source = claude_reply(persona, fan, body.get("message", ""))
                if fid is not None:
                    conn.execute("UPDATE fans SET last_days=0, messages=messages+1 "
                                 "WHERE id=?", (fid,))
                    conn.commit()
                self._json({"reply": text, "source": source})
            elif path == "/api/drop":
                title = (body.get("title") or "Untitled Drop").strip()
                price = float(body.get("price") or 0)
                tier_min = body.get("tier_min") or "Comet"
                proj = project_drop(conn, price, tier_min)
                if body.get("create"):
                    conn.execute("INSERT INTO drops(title,price,tier_min,created,sold)"
                                 " VALUES(?,?,?,datetime('now'),?)",
                                 (title, price, tier_min, proj["est_units"]))
                    conn.commit()
                self._json({"projection": proj})
            elif path == "/api/purchase":
                try:
                    fan_id = int(body.get("fan_id"))
                    drop_id = int(body.get("drop_id"))
                except (TypeError, ValueError):
                    self._json({"ok": False, "reason": "bad_request"}, 400)
                    return
                res = do_purchase(conn, fan_id, drop_id)
                self._json(res, 200 if res.get("ok") else 404)
            elif path == "/api/persona":
                conn.execute("UPDATE persona SET creator=?,voice=?,tagline=? WHERE id=1",
                             (body.get("creator", PERSONA["creator"]),
                              body.get("voice", PERSONA["voice"]),
                              body.get("tagline", PERSONA["tagline"])))
                conn.commit()
                self._json({"persona": load_persona(conn)})
            elif path == "/api/autopilot/step":
                AUTOPILOT_RUNNING["on"] = True
                res = autopilot_step(conn)
                res["running"] = AUTOPILOT_RUNNING["on"]
                self._json(res)
            elif path == "/api/autopilot/toggle":
                AUTOPILOT_RUNNING["on"] = bool(body.get("running"))
                st = autopilot_state(conn)
                st["running"] = AUTOPILOT_RUNNING["on"]
                self._json(st)
            elif path == "/api/tts":
                audio, ctype = eleven_tts(body.get("text", ""))
                if audio is None:
                    self._json({"ok": False, "reason": "no_elevenlabs"}, 200)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            else:
                self.send_error(404)
        finally:
            conn.close()


def main():
    # ensure db exists / seeded before serving
    get_conn().close()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"LoreKeeper running on http://localhost:{PORT}  "
          f"(claude={bool(ANTHROPIC_API_KEY)}, elevenlabs={bool(ELEVENLABS_API_KEY)})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
