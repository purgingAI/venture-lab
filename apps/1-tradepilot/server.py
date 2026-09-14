#!/usr/bin/env python3
"""
TradePilot -- a job-to-cash cockpit for solo skilled trades.
Python 3 stdlib only. No external deps. Serves a single-file index.html on :8801.
"""
import http.server
import socketserver
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

PORT = 8801
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "tradepilot.db")
INDEX_PATH = os.path.join(HERE, "index.html")
ENV_PATH = "/home/sagar-uniyal/creator-os/.env"

PLATFORM_FEE_RATE = 0.005  # 0.5%
DEPOSIT_RATE = 0.20  # 20% deposit collected on customer approval
CLAUDE_MODEL = "claude-sonnet-4-6"
BUSINESS_NAME = "Apex Trades"

# ---------------------------------------------------------------------------
# env loading (no external deps)
# ---------------------------------------------------------------------------
def load_env():
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env

ENV = load_env()
ANTHROPIC_API_KEY = ENV.get("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = ENV.get("ELEVENLABS_API_KEY", "")

# ---------------------------------------------------------------------------
# pricing corpus -- the compounding moat. per-trade labor + parts baselines.
# ---------------------------------------------------------------------------
PRICING_CORPUS = {
    "Plumbing": {
        "labor_rate": 95.0,
        "typical_hours": 2.0,
        "parts": {
            "Water heater (40gal)": 620.0,
            "PEX repipe section": 180.0,
            "Kitchen faucet": 145.0,
            "P-trap / drain kit": 45.0,
            "Wax ring + bolts": 18.0,
            "Shutoff valve": 22.0,
            "Sump pump": 210.0,
        },
    },
    "HVAC": {
        "labor_rate": 110.0,
        "typical_hours": 3.0,
        "parts": {
            "Condenser fan motor": 240.0,
            "Capacitor (dual run)": 65.0,
            "Refrigerant recharge (2lb)": 180.0,
            "Thermostat (smart)": 195.0,
            "Blower assembly": 320.0,
            "Air filter (MERV13)": 28.0,
        },
    },
    "Electrical": {
        "labor_rate": 105.0,
        "typical_hours": 1.5,
        "parts": {
            "200A panel upgrade": 1450.0,
            "GFCI outlet": 32.0,
            "Breaker (single pole)": 24.0,
            "EV charger (L2)": 640.0,
            "Recessed LED fixture": 38.0,
            "Whole-home surge protector": 175.0,
        },
    },
}

JOB_TYPE_KEYWORDS = {
    "Plumbing": ["water heater", "leak", "drain", "faucet", "pipe", "toilet",
                 "sump", "clog", "sewer", "valve", "flood"],
    "HVAC": ["ac", "a/c", "furnace", "hvac", "heat", "cooling", "thermostat",
             "refrigerant", "blower", "condenser", "no cool", "not cooling"],
    "Electrical": ["panel", "breaker", "outlet", "wiring", "gfci", "ev charger",
                   "electrical", "spark", "light", "circuit", "surge"],
}

STAGES = ["New", "Quoted", "Scheduled", "Invoiced", "Paid"]

# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, phone TEXT, address TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            title TEXT,
            job_type TEXT,
            photo_url TEXT,
            voice_note TEXT,
            stage TEXT DEFAULT 'New',
            approved_at TEXT,
            request_changes TEXT,
            deposit_amount REAL DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quotes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            line_items TEXT,   -- json
            parts_total REAL,
            labor_total REAL,
            total REAL,
            source TEXT,       -- AI | MOCK
            summary TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS invoices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            amount REAL,
            fee REAL,
            paid INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS inbound(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            phone TEXT,
            message TEXT,
            handled INTEGER DEFAULT 0,
            job_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS autopilot_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            inbound TEXT,
            action TEXT,
            quote_total REAL,
            job_id INTEGER
        );
        """
    )
    # backward-compatible migration for pre-existing DBs
    have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col, ddl in (("approved_at", "approved_at TEXT"),
                     ("request_changes", "request_changes TEXT"),
                     ("deposit_amount", "deposit_amount REAL DEFAULT 0")):
        if col not in have:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {ddl}")
    conn.commit()
    if fresh or conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0:
        seed(conn)
    if conn.execute("SELECT COUNT(*) c FROM inbound").fetchone()["c"] == 0:
        seed_inbound(conn)
    conn.close()

def seed_inbound(conn):
    """A small simulated queue of inbound customer messages for the Autopilot."""
    now = datetime.now(timezone.utc)
    msgs = [
        ("Brenda Cole", "512-555-0210",
         "Water heater is leaking all over the garage floor, need a quote to replace the 40 gallon tank ASAP."),
        ("Marcus Webb", "512-555-0233",
         "My AC is blowing warm air and it's 88 inside. Outside fan hums but won't spin. Can you price a fix?"),
        ("Lena Fitzgerald", "737-555-0188",
         "Half the outlets in my kitchen went dead and the GFCI won't reset. What would it cost to fix?"),
        ("Tomas Rivera", "512-555-0244",
         "Kitchen faucet is dripping constantly and the shutoff valve under the sink is corroded. Need an estimate."),
        ("Dana Whitfield", "512-555-0271",
         "Furnace won't ignite, thermostat calls for heat but just clicks. How much to get it running before the cold snap?"),
    ]
    for i, (name, phone, msg) in enumerate(msgs):
        created = (now - timedelta(minutes=(len(msgs) - i) * 3)).isoformat()
        conn.execute(
            "INSERT INTO inbound(customer,phone,message,handled,created_at) VALUES(?,?,?,0,?)",
            (name, phone, msg, created))
    conn.commit()

def seed(conn):
    now = datetime.now(timezone.utc)
    customers = [
        ("Marcy Alvarez", "512-555-0148", "1122 Cedar Ridge Dr, Austin TX"),
        ("Dev Patel", "512-555-0177", "88 Maple Ct, Round Rock TX"),
        ("Tina Okafor", "737-555-0102", "4501 Bluebonnet Ln, Austin TX"),
        ("The Hollowell Cafe", "512-555-0199", "210 Congress Ave, Austin TX"),
        ("Ray Nguyen", "512-555-0163", "9 Sunset Valley Rd, Austin TX"),
        ("Priya Shah", "512-555-0121", "77 Riverbend, Bee Cave TX"),
    ]
    cids = []
    for c in customers:
        cur = conn.execute(
            "INSERT INTO customers(name,phone,address) VALUES(?,?,?)", c)
        cids.append(cur.lastrowid)

    # (customer_idx, title, job_type, photo, voice_note, stage, days_ago)
    jobs = [
        (0, "No hot water in master bath", "Plumbing",
         "https://images.unsplash.com/photo-1585704032915-c3400ca199e7?w=600",
         "Heater's making a popping sound and the water's cold since this morning. 40 gallon tank in the garage, looks about 12 years old.",
         "Paid", 14),
        (1, "AC not cooling, warm air only", "HVAC",
         "https://images.unsplash.com/photo-1621905251189-08b45d6a269e?w=600",
         "Outside unit fan isn't spinning. It hums then stops. House is at 84 degrees.",
         "Paid", 11),
        (2, "Kitchen sink leaking under cabinet", "Plumbing",
         "https://images.unsplash.com/photo-1584622650111-993a426fbf0a?w=600",
         "Water pooling in the cabinet, smells musty. The trap looks corroded.",
         "Invoiced", 8),
        (3, "Panel keeps tripping during lunch rush", "Electrical",
         "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600",
         "Every time we run both espresso machines the main breaker trips. Need this fixed before the weekend.",
         "Scheduled", 5),
        (4, "Furnace won't ignite", "HVAC",
         "https://images.unsplash.com/photo-1635048424329-a9bfb146d7aa?w=600",
         "Thermostat calls for heat but nothing happens. Clicking noise from the unit.",
         "Quoted", 4),
        (5, "Want an EV charger installed in garage", "Electrical",
         "https://images.unsplash.com/photo-1593941707882-a5bba14938c7?w=600",
         "Just got a Tesla, need a level 2 charger. Panel is on the opposite wall, maybe 30 feet.",
         "Quoted", 3),
        (0, "Sump pump failed, basement damp", "Plumbing",
         "https://images.unsplash.com/photo-1607472586893-edb57bdc0e39?w=600",
         "Pump won't kick on, pit is filling. Storm coming this weekend.",
         "Scheduled", 2),
        (1, "Two bedroom outlets dead", "Electrical",
         "https://images.unsplash.com/photo-1621905252507-b35492cc74b4?w=600",
         "Half the outlets in the back bedrooms have no power, breakers all look fine.",
         "New", 1),
        (2, "Thermostat upgrade to smart", "HVAC",
         "https://images.unsplash.com/photo-1567925086604-4c34d18b4d20?w=600",
         "Want a Nest or Ecobee installed, old mercury thermostat right now.",
         "New", 1),
        (4, "Toilet running constantly", "Plumbing",
         "https://images.unsplash.com/photo-1620626011761-996317b8d101?w=600",
         "Guest bath toilet keeps running, wax ring might be shot too, slight leak at the base.",
         "New", 0),
        (5, "GFCI outlets in bathrooms out", "Electrical",
         "https://images.unsplash.com/photo-1558002038-1055907df827?w=600",
         "Both bathroom GFCIs tripped and won't reset. Need them replaced.",
         "Quoted", 6),
        (3, "Walk-in cooler blowing warm", "HVAC",
         "https://images.unsplash.com/photo-1517646287270-a5a9ca602e5c?w=600",
         "Cooler is at 55 degrees, should be 38. Losing product. Emergency.",
         "Invoiced", 7),
    ]

    for (ci, title, jt, photo, note, stage, days) in jobs:
        created = (now - timedelta(days=days)).isoformat()
        cur = conn.execute(
            "INSERT INTO jobs(customer_id,title,job_type,photo_url,voice_note,stage,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (cids[ci], title, jt, photo, note, stage, created))
        jid = cur.lastrowid
        # for anything past 'New', synthesize a quote so the corpus has data
        if stage != "New":
            q = build_mock_quote(jt, note)
            conn.execute(
                "INSERT INTO quotes(job_id,line_items,parts_total,labor_total,total,source,summary,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (jid, json.dumps(q["line_items"]), q["parts_total"],
                 q["labor_total"], q["total"], "SEED", q["summary"], created))
        # invoiced / paid get an invoice
        if stage in ("Invoiced", "Paid"):
            amt = q["total"]
            conn.execute(
                "INSERT INTO invoices(job_id,amount,fee,paid,created_at) VALUES(?,?,?,?,?)",
                (jid, amt, round(amt * PLATFORM_FEE_RATE, 2),
                 1 if stage == "Paid" else 0, created))
    conn.commit()

# ---------------------------------------------------------------------------
# quote engine
# ---------------------------------------------------------------------------
def classify_job(text):
    t = (text or "").lower()
    best, score = "Plumbing", 0
    for jt, kws in JOB_TYPE_KEYWORDS.items():
        s = sum(1 for k in kws if k in t)
        if s > score:
            best, score = jt, s
    return best

def build_mock_quote(job_type, note):
    corpus = PRICING_CORPUS.get(job_type, PRICING_CORPUS["Plumbing"])
    t = (note or "").lower()
    parts = corpus["parts"]
    chosen = []
    # naive part selection by keyword hits in the note
    for name, price in parts.items():
        toks = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 3]
        if any(tok in t for tok in toks):
            chosen.append((name, price))
    if not chosen:
        # fall back to the two cheapest representative parts
        chosen = sorted(parts.items(), key=lambda x: x[1])[:2]
    chosen = chosen[:3]

    line_items = []
    parts_total = 0.0
    for name, price in chosen:
        line_items.append({"kind": "part", "desc": name, "qty": 1,
                           "unit": price, "amount": price})
        parts_total += price

    hours = corpus["typical_hours"]
    # bump hours for emergency / larger jobs
    if any(w in t for w in ["emergency", "flood", "storm", "asap", "before"]):
        hours += 1.0
    labor_total = round(hours * corpus["labor_rate"], 2)
    line_items.append({"kind": "labor", "desc": f"Labor ({hours}h @ ${corpus['labor_rate']}/h)",
                       "qty": hours, "unit": corpus["labor_rate"], "amount": labor_total})

    total = round(parts_total + labor_total, 2)
    summary = (f"{job_type} service. Estimated {hours}h on site. "
               f"Parts ${parts_total:.2f} + labor ${labor_total:.2f}. "
               f"Priced from TradePilot corpus of past local jobs.")
    return {"job_type": job_type, "line_items": line_items,
            "parts_total": round(parts_total, 2), "labor_total": labor_total,
            "total": total, "source": "MOCK", "summary": summary}

def build_ai_quote(job_type, title, note):
    """Real Claude call; returns None on any failure so caller falls back."""
    if not ANTHROPIC_API_KEY:
        return None
    corpus = PRICING_CORPUS.get(job_type, {})
    prompt = (
        "You are TradePilot, a quoting assistant for a solo skilled-trades business.\n"
        f"Trade: {job_type}\n"
        f"Job title: {title}\n"
        f"Customer voice note: {note}\n\n"
        "Use this internal pricing corpus (USD) as your ground truth:\n"
        f"{json.dumps(corpus, indent=2)}\n\n"
        "Draft an itemized quote. Choose realistic parts + labor hours for THIS job. "
        "Respond with ONLY valid JSON, no prose, in this exact shape:\n"
        '{"line_items":[{"kind":"part|labor","desc":"...","qty":1,"unit":0,"amount":0}],'
        '"parts_total":0,"labor_total":0,"total":0,"summary":"one friendly sentence for the customer"}'
    )
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            data = json.loads(r.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        q = json.loads(m.group(0))
        q["job_type"] = job_type
        q["source"] = "AI"
        # trust totals if present, else recompute
        if not q.get("total"):
            q["total"] = round(q.get("parts_total", 0) + q.get("labor_total", 0), 2)
        return q
    except Exception as e:
        print("[ai] fallback:", repr(e))
        return None

def make_quote(job_type, title, note):
    q = build_ai_quote(job_type, title, note)
    if q:
        return q
    return build_mock_quote(job_type, note)

# ---------------------------------------------------------------------------
# tts (ElevenLabs) -- optional
# ---------------------------------------------------------------------------
def tts_bytes(text):
    if not ELEVENLABS_API_KEY:
        return None
    voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({
        "text": text[:500],
        "model_id": "eleven_turbo_v2",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.7},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "xi-api-key": ELEVENLABS_API_KEY,
        "content-type": "application/json",
        "accept": "audio/mpeg",
    }, method="POST")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.read()
    except Exception as e:
        print("[tts] fail:", repr(e))
        return None

# ---------------------------------------------------------------------------
# data assembly for the UI
# ---------------------------------------------------------------------------
def get_state():
    conn = db()
    jobs = []
    for j in conn.execute(
        "SELECT j.*, c.name cust_name, c.phone cust_phone, c.address cust_addr "
        "FROM jobs j JOIN customers c ON c.id=j.customer_id ORDER BY j.created_at DESC"):
        job = dict(j)
        q = conn.execute(
            "SELECT * FROM quotes WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job["id"],)).fetchone()
        if q:
            job["quote"] = dict(q)
            job["quote"]["line_items"] = json.loads(q["line_items"])
        inv = conn.execute(
            "SELECT * FROM invoices WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job["id"],)).fetchone()
        if inv:
            job["invoice"] = dict(inv)
        jobs.append(job)

    # pricing intelligence: learned avg total + avg labor hrs per job type
    intel = {}
    rows = conn.execute(
        "SELECT j.job_type jt, AVG(q.total) avg_total, AVG(q.parts_total) avg_parts, "
        "AVG(q.labor_total) avg_labor, COUNT(*) n "
        "FROM quotes q JOIN jobs j ON j.id=q.job_id GROUP BY j.job_type").fetchall()
    for r in rows:
        intel[r["jt"]] = {
            "avg_total": round(r["avg_total"] or 0, 2),
            "avg_parts": round(r["avg_parts"] or 0, 2),
            "avg_labor": round(r["avg_labor"] or 0, 2),
            "samples": r["n"],
        }

    inv_rows = conn.execute("SELECT * FROM invoices").fetchall()
    collected = round(sum(i["amount"] for i in inv_rows if i["paid"]), 2)
    outstanding = round(sum(i["amount"] for i in inv_rows if not i["paid"]), 2)
    fees = round(sum(i["fee"] for i in inv_rows if i["paid"]), 2)

    deposits = round(sum(j.get("deposit_amount") or 0 for j in jobs), 2)

    conn.close()
    return {
        "jobs": jobs,
        "intel": intel,
        "corpus": PRICING_CORPUS,
        "stages": STAGES,
        "business_name": BUSINESS_NAME,
        "totals": {"collected": collected, "outstanding": outstanding,
                   "fees": fees, "fee_rate": PLATFORM_FEE_RATE,
                   "deposits": deposits, "deposit_rate": DEPOSIT_RATE},
        "capabilities": {"ai": bool(ANTHROPIC_API_KEY), "tts": bool(ELEVENLABS_API_KEY)},
    }

def advance_stage(job_id, direction):
    conn = db()
    j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        conn.close()
        return {"error": "job not found"}
    idx = STAGES.index(j["stage"])
    idx = max(0, min(len(STAGES) - 1, idx + direction))
    new_stage = STAGES[idx]
    conn.execute("UPDATE jobs SET stage=? WHERE id=?", (new_stage, job_id))
    # side effects: entering Invoiced creates an invoice if none
    if new_stage == "Invoiced":
        q = conn.execute("SELECT * FROM quotes WHERE job_id=? ORDER BY id DESC LIMIT 1",
                         (job_id,)).fetchone()
        amt = q["total"] if q else 0.0
        existing = conn.execute("SELECT * FROM invoices WHERE job_id=?", (job_id,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO invoices(job_id,amount,fee,paid,created_at) VALUES(?,?,?,0,?)",
                (job_id, amt, round(amt * PLATFORM_FEE_RATE, 2), datetime.now(timezone.utc).isoformat()))
    if new_stage == "Paid":
        conn.execute("UPDATE invoices SET paid=1 WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "stage": new_stage}

# ---------------------------------------------------------------------------
# customer-facing quote approval
# ---------------------------------------------------------------------------
def get_public_quote(job_id):
    """Everything the shareable /q/<id> page needs (or None)."""
    conn = db()
    j = conn.execute(
        "SELECT j.*, c.name cust_name FROM jobs j "
        "JOIN customers c ON c.id=j.customer_id WHERE j.id=?", (job_id,)).fetchone()
    if not j:
        conn.close()
        return None
    q = conn.execute(
        "SELECT * FROM quotes WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,)).fetchone()
    conn.close()
    job = dict(j)
    quote = None
    if q:
        quote = dict(q)
        quote["line_items"] = json.loads(q["line_items"])
    return {
        "job_id": job["id"],
        "title": job["title"],
        "job_type": job["job_type"],
        "customer": job["cust_name"],
        "stage": job["stage"],
        "approved_at": job["approved_at"],
        "deposit_amount": job["deposit_amount"] or 0,
        "business_name": BUSINESS_NAME,
        "quote": quote,
    }

def approve_quote(job_id):
    conn = db()
    j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        conn.close()
        return {"error": "job not found"}, HTTPStatus.NOT_FOUND
    if j["approved_at"]:
        conn.close()
        return {"ok": True, "already": True, "stage": j["stage"],
                "approved_at": j["approved_at"],
                "deposit_amount": j["deposit_amount"] or 0}, HTTPStatus.OK
    q = conn.execute("SELECT * FROM quotes WHERE job_id=? ORDER BY id DESC LIMIT 1",
                     (job_id,)).fetchone()
    total = q["total"] if q else 0.0
    deposit = round(total * DEPOSIT_RATE, 2)
    now = datetime.now(timezone.utc).isoformat()
    # advance to Scheduled, record approval + collected deposit
    idx = STAGES.index("Scheduled")
    conn.execute(
        "UPDATE jobs SET stage=?, approved_at=?, deposit_amount=?, request_changes=NULL WHERE id=?",
        (STAGES[idx], now, deposit, job_id))
    conn.commit()
    conn.close()
    # simulated confirmation (SMS/email)
    print(f"[approve] job {job_id} approved by customer -> Scheduled; "
          f"deposit ${deposit:.2f} collected; confirmation sent.")
    return {"ok": True, "stage": "Scheduled", "approved_at": now,
            "deposit_amount": deposit,
            "confirmation": f"Confirmation sent to the customer. {BUSINESS_NAME} "
                            f"will be in touch to schedule."}, HTTPStatus.OK

def request_quote_changes(job_id, message):
    conn = db()
    j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        conn.close()
        return {"error": "job not found"}, HTTPStatus.NOT_FOUND
    now = datetime.now(timezone.utc).isoformat()
    note = (message or "Customer requested changes to the quote.").strip()
    conn.execute("UPDATE jobs SET request_changes=? WHERE id=?",
                 (f"{now}|{note}", job_id))
    conn.commit()
    conn.close()
    print(f"[changes] job {job_id}: {note}")
    return {"ok": True, "message": "Your request was sent to the business."}, HTTPStatus.OK

# ---------------------------------------------------------------------------
# ambient mode simulation
# ---------------------------------------------------------------------------
def ambient_reply(inbound):
    jt = classify_job(inbound)
    corpus = PRICING_CORPUS.get(jt, PRICING_CORPUS["Plumbing"])
    lo = round(corpus["typical_hours"] * corpus["labor_rate"])
    hi = round(lo + min(corpus["parts"].values()) + max(corpus["parts"].values()) / 2)
    slots = ["tomorrow 9-11am", "tomorrow 1-3pm", "Thursday 8-10am"]
    steps = [
        {"actor": "customer", "text": inbound},
        {"actor": "agent", "text": f"Hi! This is TradePilot for Apex Trades. Sounds like a {jt.lower()} issue - I can help right now."},
        {"actor": "agent", "text": f"Based on similar jobs nearby, this typically runs ${lo}-${hi}. I've flagged it for a firm quote after a quick look."},
        {"actor": "agent", "text": f"I have {slots[0]} or {slots[1]} open. Which works? I'll hold the slot and text you a confirmation + the tech's name."},
        {"actor": "system", "text": f"Draft job created ({jt}) -> pipeline: New. Zero taps from the owner."},
    ]
    return {"job_type": jt, "est_low": lo, "est_high": hi, "steps": steps}

# ---------------------------------------------------------------------------
# autopilot -- hands-free inbound lead handling
# ---------------------------------------------------------------------------
def autopilot_state():
    conn = db()
    handled = conn.execute("SELECT COUNT(*) c FROM inbound WHERE handled=1").fetchone()["c"]
    remaining = conn.execute("SELECT COUNT(*) c FROM inbound WHERE handled=0").fetchone()["c"]
    conn.close()
    return {"handled_count": handled, "queue_remaining": remaining,
            "running": remaining > 0}

def autopilot_log():
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM autopilot_log ORDER BY id DESC LIMIT 50").fetchall()]
    conn.close()
    return {"log": rows}

def autopilot_step():
    """One cycle: classify + quote the next unhandled inbound, create a
    Quoted job with a public /q/<id> link, mark handled, and log it."""
    conn = db()
    lead = conn.execute(
        "SELECT * FROM inbound WHERE handled=0 ORDER BY id ASC LIMIT 1").fetchone()
    if not lead:
        conn.close()
        return {"idle": True, "message": "Queue empty — Autopilot standing by.",
                "state": autopilot_state()}
    lead = dict(lead)
    conn.close()

    msg = lead["message"]
    cust = lead["customer"] or "Inbound customer"
    job_type = classify_job(msg)
    title = _lead_title(msg, job_type)
    q = make_quote(job_type, title, msg)
    now = datetime.now(timezone.utc).isoformat()

    conn = db()
    c = conn.execute("SELECT id FROM customers WHERE name=?", (cust,)).fetchone()
    cid = c["id"] if c else conn.execute(
        "INSERT INTO customers(name,phone,address) VALUES(?,?,?)",
        (cust, lead.get("phone", ""), "")).lastrowid
    jid = conn.execute(
        "INSERT INTO jobs(customer_id,title,job_type,photo_url,voice_note,stage,created_at) "
        "VALUES(?,?,?,?,?,'Quoted',?)",
        (cid, title, job_type, "", msg, now)).lastrowid
    conn.execute(
        "INSERT INTO quotes(job_id,line_items,parts_total,labor_total,total,source,summary,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (jid, json.dumps(q["line_items"]), q.get("parts_total", 0),
         q.get("labor_total", 0), q.get("total", 0), q.get("source", "MOCK"),
         q.get("summary", ""), now))
    conn.execute("UPDATE inbound SET handled=1, job_id=? WHERE id=?", (jid, lead["id"]))
    action = f"Quoted {job_type} job '{title}' for {cust} — link ready"
    conn.execute(
        "INSERT INTO autopilot_log(ts,inbound,action,quote_total,job_id) VALUES(?,?,?,?,?)",
        (now, msg, action, q.get("total", 0), jid))
    conn.commit()
    conn.close()
    print(f"[autopilot] inbound {lead['id']} -> job {jid} ({job_type}) "
          f"quoted ${q.get('total', 0):.2f}; /q/{jid} ready.")
    return {
        "ok": True,
        "inbound": msg,
        "customer": cust,
        "job_id": jid,
        "job_type": job_type,
        "title": title,
        "quote_total": q.get("total", 0),
        "source": q.get("source", "MOCK"),
        "quote_link": f"/q/{jid}",
        "action": action,
        "state": autopilot_state(),
    }

def _lead_title(msg, job_type):
    """A short human title from the inbound message via Claude, mock fallback."""
    t = build_ai_lead_title(msg, job_type)
    if t:
        return t
    # mock: first clause, capped
    clause = re.split(r"[.,;\n]", msg.strip())[0].strip()
    if len(clause) > 60:
        clause = clause[:57].rstrip() + "..."
    return clause or f"{job_type} service request"

def build_ai_lead_title(msg, job_type):
    if not ANTHROPIC_API_KEY:
        return None
    prompt = (
        "You are TradePilot triaging an inbound customer message for a skilled-trades business.\n"
        f"Trade: {job_type}\n"
        f"Message: {msg}\n\n"
        "Write a short job title (max 8 words, no quotes, no trailing period). "
        "Respond with ONLY the title text."
    )
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 40,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method="POST")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            data = json.loads(r.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        text = text.strip().strip('"').strip().rstrip(".")
        return text[:70] if text else None
    except Exception as e:
        print("[autopilot ai title] fallback:", repr(e))
        return None

# ---------------------------------------------------------------------------
# public quote page (its own lighter styling, same amber brand)
# ---------------------------------------------------------------------------
def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def render_quote_page(d):
    biz = _esc(d["business_name"])
    q = d.get("quote")
    approved = bool(d.get("approved_at"))
    deposit = d.get("deposit_amount") or 0
    total = (q["total"] if q else 0) or 0

    if q:
        rows = "".join(
            f'<div class="li{" labor" if li.get("kind")=="labor" else ""}">'
            f'<span>{_esc(li.get("desc",""))}</span>'
            f'<span class="a">${float(li.get("amount",0)):,.2f}</span></div>'
            for li in q["line_items"])
        parts_total = f'${float(q.get("parts_total",0)):,.2f}'
        labor_total = f'${float(q.get("labor_total",0)):,.2f}'
        summary = _esc(q.get("summary", ""))
    else:
        rows = '<div class="li"><span>Quote is being prepared…</span><span></span></div>'
        parts_total = labor_total = "$0.00"
        summary = ""

    approved_banner = ""
    if approved:
        approved_banner = (
            '<div class="banner ok">✓ You approved this quote. '
            f'A {int(DEPOSIT_RATE*100)}% deposit of ${deposit:,.2f} was collected and '
            f'{biz} will reach out to schedule.</div>')

    actions = "" if approved else f"""
      <div class="actions">
        <button class="btn primary" id="approveBtn">Approve &amp; Schedule</button>
        <button class="btn ghost" id="changesBtn">Request changes</button>
      </div>
      <p class="fineprint">Approving collects a {int(DEPOSIT_RATE*100)}% deposit
        (${total*DEPOSIT_RATE:,.2f}) to reserve your slot. The balance is due on completion.</p>
    """

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your quote from {biz}</title>
<style>
:root{{--amber:#f59e0b;--amber-dim:#b45309;--ink:#20242c;--muted:#6b7280;
  --line:#ececf1;--good:#059669;--paper:#ffffff;--bg:#f6f7f9;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font-family:'Segoe UI',system-ui,-apple-system,Roboto,sans-serif;line-height:1.5;
  padding:22px 14px 60px}}
.card{{max-width:520px;margin:0 auto;background:var(--paper);border:1px solid var(--line);
  border-radius:18px;overflow:hidden;box-shadow:0 12px 40px rgba(20,24,32,.08)}}
.top{{background:linear-gradient(135deg,var(--amber),var(--amber-dim));color:#1a1205;padding:22px 24px}}
.brand{{display:flex;align-items:center;gap:11px}}
.mark{{width:38px;height:38px;border-radius:10px;background:rgba(26,18,5,.16);display:grid;place-items:center}}
.mark svg{{width:22px;height:22px}}
.brand .name{{font-size:18px;font-weight:800;letter-spacing:.3px}}
.brand .sub{{font-size:11px;letter-spacing:2px;text-transform:uppercase;opacity:.75}}
.body{{padding:22px 24px 26px}}
h2{{font-size:15px;margin:0 0 4px}}
.for{{font-size:12.5px;color:var(--muted);margin-bottom:18px}}
.li{{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px dashed var(--line);font-size:14px}}
.li.labor span:first-child{{color:var(--amber-dim);font-weight:600}}
.li .a{{font-variant-numeric:tabular-nums;color:var(--ink)}}
.subtot{{display:flex;justify-content:space-between;font-size:12.5px;color:var(--muted);padding:6px 0}}
.total{{display:flex;justify-content:space-between;margin-top:12px;padding-top:12px;
  border-top:2px solid var(--ink);font-size:20px;font-weight:800}}
.total .v{{color:var(--amber-dim)}}
.summary{{font-size:13px;color:var(--muted);font-style:italic;margin:16px 0 6px}}
.actions{{display:flex;gap:10px;margin-top:22px}}
.btn{{flex:1;border:none;border-radius:11px;padding:14px;font-size:14px;font-weight:700;
  cursor:pointer;transition:.15s;font-family:inherit}}
.btn.primary{{background:linear-gradient(135deg,var(--amber),var(--amber-dim));color:#1a1205;
  box-shadow:0 6px 18px rgba(245,158,11,.3)}}
.btn.primary:hover{{transform:translateY(-1px)}}
.btn.ghost{{background:#fff;color:var(--amber-dim);border:1.5px solid var(--amber)}}
.btn:disabled{{opacity:.55;cursor:default;transform:none}}
.fineprint{{font-size:11.5px;color:var(--muted);margin-top:12px;text-align:center}}
.banner{{border-radius:12px;padding:13px 15px;font-size:13px;margin-bottom:18px}}
.banner.ok{{background:#ecfdf5;border:1px solid #a7f3d0;color:var(--good)}}
.foot{{text-align:center;font-size:11px;color:var(--muted);margin-top:24px}}
.foot b{{color:var(--amber-dim)}}
.spin{{display:inline-block;width:14px;height:14px;border:2px solid rgba(0,0,0,.2);
  border-top-color:#1a1205;border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px}}
@keyframes sp{{to{{transform:rotate(360deg)}}}}
</style></head><body>
<div class="card">
  <div class="top">
    <div class="brand">
      <div class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#1a1205" stroke-width="2.2"
        stroke-linecap="round" stroke-linejoin="round"><path d="M3 12l4 4 8-9"/><path d="M14 4l7 7-4 4"/></svg></div>
      <div><div class="name">{biz}</div><div class="sub">Your Service Quote</div></div>
    </div>
  </div>
  <div class="body">
    <div id="banner">{approved_banner}</div>
    <h2>{_esc(d["title"])}</h2>
    <div class="for">Prepared for {_esc(d["customer"])} · {_esc(d["job_type"])}</div>
    {rows}
    <div class="subtot"><span>Parts</span><span>{parts_total}</span></div>
    <div class="subtot"><span>Labor</span><span>{labor_total}</span></div>
    <div class="total"><span>Total</span><span class="v">${total:,.2f}</span></div>
    <div class="summary">{summary}</div>
    <div id="actionArea">{actions}</div>
  </div>
</div>
<div class="foot">Powered by <b>TradePilot</b></div>
<script>
const JOB_ID={d["job_id"]};
async function post(path,body){{
  const r=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});
  return r.json();
}}
const ab=document.getElementById('approveBtn');
if(ab) ab.addEventListener('click',async()=>{{
  ab.disabled=true;ab.innerHTML='<span class="spin"></span> Approving…';
  const r=await post('/api/approve_quote',{{job_id:JOB_ID}});
  document.getElementById('actionArea').innerHTML='';
  document.getElementById('banner').innerHTML=
    '<div class="banner ok">✓ Approved! A {int(DEPOSIT_RATE*100)}% deposit of $'+
    (r.deposit_amount||0).toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}})+
    ' was collected and {biz} will reach out to schedule.</div>';
}});
const cb=document.getElementById('changesBtn');
if(cb) cb.addEventListener('click',async()=>{{
  const msg=prompt('What would you like changed on this quote?');
  if(msg===null) return;
  cb.disabled=true;
  const r=await post('/api/request_changes',{{job_id:JOB_ID,message:msg}});
  cb.textContent='Sent ✓';
}});
</script>
</body></html>"""

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep it quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "index.html missing"})
        elif path == "/api/state":
            self._send(HTTPStatus.OK, get_state())
        elif path == "/api/autopilot/state":
            self._send(HTTPStatus.OK, autopilot_state())
        elif path == "/api/autopilot/log":
            self._send(HTTPStatus.OK, autopilot_log())
        elif path == "/api/health":
            self._send(HTTPStatus.OK, {"ok": True, "caps": {
                "ai": bool(ANTHROPIC_API_KEY), "tts": bool(ELEVENLABS_API_KEY)}})
        elif path.startswith("/q/"):
            m = re.match(r"^/q/(\d+)$", path)
            if not m:
                self._send(HTTPStatus.NOT_FOUND, "Quote not found", "text/html; charset=utf-8")
                return
            data = get_public_quote(int(m.group(1)))
            if not data:
                self._send(HTTPStatus.NOT_FOUND, "Quote not found", "text/html; charset=utf-8")
                return
            self._send(HTTPStatus.OK, render_quote_page(data), "text/html; charset=utf-8")
        elif path.startswith("/api/public_quote/"):
            m = re.match(r"^/api/public_quote/(\d+)$", path)
            data = get_public_quote(int(m.group(1))) if m else None
            if not data:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send(HTTPStatus.OK, data)
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        data = self._read_json()

        if path == "/api/quote":
            title = data.get("title", "").strip() or "Untitled job"
            note = data.get("voice_note", "").strip()
            photo = data.get("photo_url", "").strip()
            cust = data.get("customer", "Walk-in customer").strip() or "Walk-in customer"
            job_type = data.get("job_type") or classify_job(title + " " + note)
            q = make_quote(job_type, title, note)

            conn = db()
            c = conn.execute("SELECT id FROM customers WHERE name=?", (cust,)).fetchone()
            cid = c["id"] if c else conn.execute(
                "INSERT INTO customers(name,phone,address) VALUES(?,?,?)",
                (cust, "", "")).lastrowid
            jid = conn.execute(
                "INSERT INTO jobs(customer_id,title,job_type,photo_url,voice_note,stage,created_at) "
                "VALUES(?,?,?,?,?,'Quoted',?)",
                (cid, title, job_type, photo, note, datetime.now(timezone.utc).isoformat())).lastrowid
            conn.execute(
                "INSERT INTO quotes(job_id,line_items,parts_total,labor_total,total,source,summary,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (jid, json.dumps(q["line_items"]), q.get("parts_total", 0),
                 q.get("labor_total", 0), q.get("total", 0), q.get("source", "MOCK"),
                 q.get("summary", ""), datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
            q["job_id"] = jid
            q["job_type"] = job_type
            self._send(HTTPStatus.OK, q)

        elif path == "/api/advance":
            r = advance_stage(int(data.get("job_id", 0)), int(data.get("direction", 1)))
            self._send(HTTPStatus.OK, r)

        elif path == "/api/set_stage":
            job_id = int(data.get("job_id", 0))
            target = data.get("stage", "New")
            conn = db()
            j = conn.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.close()
            if not j or target not in STAGES:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "bad stage"})
                return
            cur = STAGES.index(j["stage"])
            tgt = STAGES.index(target)
            advance_stage(job_id, tgt - cur)
            self._send(HTTPStatus.OK, {"ok": True, "stage": target})

        elif path == "/api/approve_quote":
            resp, code = approve_quote(int(data.get("job_id", 0)))
            self._send(code, resp)

        elif path == "/api/request_changes":
            resp, code = request_quote_changes(
                int(data.get("job_id", 0)), data.get("message", ""))
            self._send(code, resp)

        elif path == "/api/autopilot/step":
            self._send(HTTPStatus.OK, autopilot_step())

        elif path == "/api/ambient":
            inbound = data.get("text", "").strip() or "My AC stopped cooling and it's 90 degrees inside, help!"
            self._send(HTTPStatus.OK, ambient_reply(inbound))

        elif path == "/tts":
            text = data.get("text", "").strip()
            audio = tts_bytes(text)
            if audio is None:
                self._send(HTTPStatus.OK, {"fallback": "speechSynthesis"})
            else:
                self._send(HTTPStatus.OK, audio, "audio/mpeg")
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    init_db()
    caps = []
    if ANTHROPIC_API_KEY:
        caps.append("Claude quote AI")
    if ELEVENLABS_API_KEY:
        caps.append("ElevenLabs TTS")
    print(f"TradePilot on http://localhost:{PORT}  |  capabilities: "
          f"{', '.join(caps) if caps else 'MOCK only'}")
    with ThreadingServer(("0.0.0.0", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")


if __name__ == "__main__":
    main()
