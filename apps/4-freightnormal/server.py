#!/usr/bin/env python3
"""FreightNormal — freight quote normalization console.

Python 3 stdlib only. Serves a single-file index.html and a small JSON API
on PORT 8804. SQLite is seeded on first run with historical normalized quotes.

If ANTHROPIC_API_KEY is present in ~/creator-os/.env, /api/normalize will use a
real Claude call to parse messy pasted quote text; otherwise a regex/heuristic
MOCK parser runs fully offline.
"""
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8804
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "freightnormal.db")
INDEX_PATH = os.path.join(HERE, "index.html")
ENV_PATH = os.path.expanduser("~/creator-os/.env")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# ANTHROPIC key loading (best-effort, never fatal)
# ---------------------------------------------------------------------------

def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY"):
                    _, _, val = line.partition("=")
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return None


API_KEY = load_api_key()

# ---------------------------------------------------------------------------
# Seed corpus — ~12 normalized historical quotes across a few lanes
# ---------------------------------------------------------------------------

SEED = [
    # origin, dest, miles, carrier, linehaul, fuel, accessorials, total
    ("Chicago, IL", "Dallas, TX", 925, "Blue Ridge Logistics", 1480.0, 310.0, 75.0, 1865.0),
    ("Chicago, IL", "Dallas, TX", 925, "Summit Freightways",   1395.0, 298.0, 120.0, 1813.0),
    ("Chicago, IL", "Dallas, TX", 925, "IronHaul Carriers",    1560.0, 335.0, 60.0, 1955.0),
    ("Atlanta, GA", "Miami, FL",  665, "Coastal Express",       980.0, 205.0, 90.0, 1275.0),
    ("Atlanta, GA", "Miami, FL",  665, "Blue Ridge Logistics",  1020.0, 214.0, 45.0, 1279.0),
    ("Atlanta, GA", "Miami, FL",  665, "Palmetto Transport",    935.0, 198.0, 130.0, 1263.0),
    ("Los Angeles, CA", "Phoenix, AZ", 375, "Desert Line Freight", 640.0, 118.0, 40.0, 798.0),
    ("Los Angeles, CA", "Phoenix, AZ", 375, "IronHaul Carriers",   690.0, 128.0, 25.0, 843.0),
    ("Los Angeles, CA", "Phoenix, AZ", 375, "Pacific Draymen",     615.0, 112.0, 80.0, 807.0),
    ("Newark, NJ", "Columbus, OH", 545, "Summit Freightways",   870.0, 176.0, 55.0, 1101.0),
    ("Newark, NJ", "Columbus, OH", 545, "Keystone Carriers",    905.0, 182.0, 30.0, 1117.0),
    ("Newark, NJ", "Columbus, OH", 545, "Coastal Express",      840.0, 170.0, 95.0, 1105.0),
]

# ~5 open loads needing a carrier — origin, dest, target sell rate to shipper
OPEN_LOAD_SEED = [
    ("Chicago, IL", "Dallas, TX", 2150.0),
    ("Atlanta, GA", "Miami, FL", 1480.0),
    ("Los Angeles, CA", "Phoenix, AZ", 900.0),
    ("Newark, NJ", "Columbus, OH", 1180.0),
    ("Chicago, IL", "Dallas, TX", 1950.0),
]

AUTOPILOT_MARGIN_THRESHOLD = 12.0  # percent


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quotes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, dest TEXT, miles INTEGER, carrier TEXT,
            linehaul REAL, fuel REAL, accessorials REAL, total REAL,
            per_mile REAL, created TEXT DEFAULT (datetime('now'))
        )"""
    )
    # additive migration: book-and-tender loads
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            load_number TEXT, origin TEXT, dest TEXT, miles INTEGER, carrier TEXT,
            linehaul REAL, fuel REAL, accessorials REAL, total REAL, per_mile REAL,
            sell REAL, margin REAL, margin_pct REAL,
            status TEXT DEFAULT 'Tendered',
            accessorial_items TEXT,
            created TEXT DEFAULT (datetime('now'))
        )"""
    )
    # additive migration: saved-lane watchlist
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchlist(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, dest TEXT,
            created TEXT DEFAULT (datetime('now')),
            UNIQUE(origin, dest)
        )"""
    )
    # additive migration: autopilot open-load queue (loads needing a carrier)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS open_loads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT, dest TEXT, target_sell REAL,
            status TEXT DEFAULT 'open',
            load_number TEXT,
            created TEXT DEFAULT (datetime('now'))
        )"""
    )
    # additive migration: autopilot decision log
    conn.execute(
        """CREATE TABLE IF NOT EXISTS autopilot_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            lane TEXT, carrier TEXT, rate REAL, margin_pct REAL,
            action TEXT, load_number TEXT, rationale TEXT
        )"""
    )
    # seed the open-load queue once (additive, only if empty)
    if conn.execute("SELECT COUNT(*) c FROM open_loads").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO open_loads(origin,dest,target_sell) VALUES(?,?,?)",
            OPEN_LOAD_SEED,
        )
    if fresh:
        rows = []
        for (o, d, m, c, lh, fu, ac, tot) in SEED:
            pm = round(tot / m, 3) if m else 0.0
            rows.append((o, d, m, c, lh, fu, ac, tot, pm))
        conn.executemany(
            "INSERT INTO quotes(origin,dest,miles,carrier,linehaul,fuel,accessorials,total,per_mile)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    conn.close()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Normalized schema helpers
# ---------------------------------------------------------------------------

NORMALIZED_KEYS = ["origin", "dest", "miles", "carrier",
                   "linehaul", "fuel", "accessorials", "total"]


def coerce_schema(d):
    """Force a raw dict into the standard normalized schema with derived fields."""
    out = {
        "origin": str(d.get("origin") or "Unknown origin").strip(),
        "dest": str(d.get("dest") or d.get("destination") or "Unknown dest").strip(),
        "miles": _num(d.get("miles"), 0),
        "carrier": str(d.get("carrier") or "Unknown carrier").strip(),
        "linehaul": _num(d.get("linehaul"), 0.0),
        "fuel": _num(d.get("fuel") or d.get("fuel_surcharge"), 0.0),
        "accessorials": _num(d.get("accessorials"), 0.0),
    }
    total = d.get("total")
    if total in (None, "", 0):
        total = out["linehaul"] + out["fuel"] + out["accessorials"]
    out["total"] = round(_num(total, 0.0), 2)
    out["per_mile"] = round(out["total"] / out["miles"], 3) if out["miles"] else 0.0
    # keep any itemized accessorial breakdown the parser found
    if isinstance(d.get("accessorial_items"), list):
        out["accessorial_items"] = d["accessorial_items"]
    return out


def _num(v, default):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^0-9.\-]", "", str(v))
    if s in ("", "-", "."):
        return default
    try:
        f = float(s)
        return int(f) if isinstance(default, int) else f
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# MOCK heuristic parser (offline fallback)
# ---------------------------------------------------------------------------

ACCESSORIAL_HINTS = [
    "detention", "layover", "liftgate", "lift gate", "residential", "inside delivery",
    "tarp", "reweigh", "reconsignment", "driver assist", "notify", "appointment",
    "sort", "segregate", "hazmat", "team", "stop off", "stop-off", "pallet jack",
]


def mock_parse(text):
    t = text or ""
    low = t.lower()

    # carrier
    carrier = _find(t, [
        r"carrier[:\s]+([A-Za-z0-9 &\.\-]{3,40})",
        r"quote from[:\s]+([A-Za-z0-9 &\.\-]{3,40})",
        r"^([A-Z][A-Za-z0-9 &\.\-]{3,40})(?: quote| logistics| freight| carriers| transport| express)",
    ])
    if carrier:
        carrier = re.split(r"\b(quote|attn|ref|date)\b", carrier, flags=re.I)[0].strip(" -:\n")

    # lane origin -> dest
    origin = dest = None
    lane = re.search(
        r"([A-Za-z\. ]+,\s*[A-Z]{2})\s*(?:->|→|to|-|=>|\bto\b)\s*([A-Za-z\. ]+,\s*[A-Z]{2})",
        t)
    if lane:
        origin, dest = lane.group(1).strip(), lane.group(2).strip()
    if not origin:
        origin = _find(t, [r"origin[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})",
                           r"from[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})",
                           r"pickup[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})"])
    if not dest:
        dest = _find(t, [r"dest(?:ination)?[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})",
                         r"deliver(?:y)?[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})",
                         r"drop[:\s]+([A-Za-z\. ]+,\s*[A-Z]{2})"])

    miles = _num(_find(t, [r"([\d,]+)\s*mi(?:les)?\b", r"distance[:\s]+([\d,]+)",
                           r"([\d,]+)\s*mi\b"]), 0)

    linehaul = _money_near(t, ["linehaul", "line haul", "line-haul", "base rate",
                               "base freight", "flat rate", "freight charge"])
    fuel = _money_near(t, ["fuel surcharge", "fuel", "fsc", "fuel sur"])

    # accessorials — sum any line that matches a hint and has a dollar amount
    acc_items = []
    acc_total = 0.0
    for line in re.split(r"[\n;]+", t):
        ll = line.lower()
        if any(h in ll for h in ACCESSORIAL_HINTS):
            amt = _first_money(line)
            if amt is not None:
                label = re.sub(r"[\$\d,\.\s]+$", "", line).strip(" -:\t")
                acc_items.append({"label": label or "accessorial", "amount": amt})
                acc_total += amt

    total = _money_near(t, ["grand total", "total due", "all-in", "all in", "total"])

    d = {
        "origin": origin, "dest": dest, "miles": miles, "carrier": carrier,
        "linehaul": linehaul, "fuel": fuel, "accessorials": round(acc_total, 2),
        "total": total,
    }
    if acc_items:
        d["accessorial_items"] = acc_items
    parsed = coerce_schema(d)
    parsed["_engine"] = "mock-heuristic"
    return parsed


def _find(text, patterns):
    for p in patterns:
        m = re.search(p, text, flags=re.I | re.M)
        if m:
            return m.group(1).strip()
    return None


def _first_money(s):
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _money_near(text, labels):
    for lab in labels:
        m = re.search(re.escape(lab) + r"[^\d\-$]{0,20}\$?\s*([\d,]+(?:\.\d{1,2})?)",
                      text, flags=re.I)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return 0.0


# ---------------------------------------------------------------------------
# Claude parser (real API when key present)
# ---------------------------------------------------------------------------

CLAUDE_PROMPT = (
    "You are a freight-quote normalization engine. Extract the following raw carrier "
    "quote text into a STRICT JSON object with exactly these keys: origin (string "
    "'City, ST'), dest (string 'City, ST'), miles (integer), carrier (string), "
    "linehaul (number USD), fuel (number USD fuel surcharge), accessorials (number USD, "
    "the SUM of all accessorial/extra charges), accessorial_items (array of "
    "{label, amount}), total (number USD all-in). If a value is missing, infer "
    "reasonably or use 0. Respond with ONLY the JSON object, no prose.\n\nRAW QUOTE:\n"
)


def claude_parse(text):
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": CLAUDE_PROMPT + text}],
    }).encode()
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    txt = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", txt, flags=re.S)
    raw = json.loads(m.group(0) if m else txt)
    parsed = coerce_schema(raw)
    parsed["_engine"] = "claude:" + CLAUDE_MODEL
    return parsed


def normalize(text):
    if API_KEY:
        try:
            return claude_parse(text)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError,
                json.JSONDecodeError, TimeoutError):
            r = mock_parse(text)
            r["_engine"] += " (ai-fallback)"
            return r
    return mock_parse(text)


# ---------------------------------------------------------------------------
# Lane intelligence
# ---------------------------------------------------------------------------

def lane_intel():
    conn = db()
    rows = conn.execute(
        """SELECT origin, dest, COUNT(*) n, AVG(miles) miles,
                  AVG(per_mile) avg_per_mile, MIN(total) min_total,
                  AVG(total) avg_total, MAX(total) max_total
           FROM quotes GROUP BY origin, dest ORDER BY n DESC"""
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "lane": f"{r['origin']} → {r['dest']}",
            "origin": r["origin"], "dest": r["dest"],
            "n": r["n"], "miles": round(r["miles"]),
            "avg_per_mile": round(r["avg_per_mile"], 3),
            "min_total": round(r["min_total"], 2),
            "avg_total": round(r["avg_total"], 2),
            "max_total": round(r["max_total"], 2),
        })
    return out


def market_verdict(parsed):
    """Compare a normalized quote's per-mile against corpus for its lane."""
    conn = db()
    row = conn.execute(
        "SELECT AVG(per_mile) apm, COUNT(*) n FROM quotes WHERE origin=? AND dest=?",
        (parsed.get("origin"), parsed.get("dest")),
    ).fetchone()
    if not row or not row["n"]:
        row = conn.execute("SELECT AVG(per_mile) apm, COUNT(*) n FROM quotes").fetchone()
        scope = "corpus-wide"
    else:
        scope = "this lane"
    conn.close()
    market = round(row["apm"], 3) if row and row["apm"] else 0.0
    pm = parsed.get("per_mile", 0.0)
    if not market:
        return {"market_per_mile": 0.0, "scope": scope, "verdict": "no data", "delta_pct": 0.0}
    delta = round((pm - market) / market * 100, 1)
    if delta <= -5:
        verdict = "below market"
    elif delta >= 5:
        verdict = "above market"
    else:
        verdict = "at market"
    return {"market_per_mile": market, "scope": scope, "verdict": verdict, "delta_pct": delta}


def persist_quote(parsed):
    """Add a newly-normalized quote to the compounding corpus."""
    if not parsed.get("miles"):
        return
    conn = db()
    conn.execute(
        "INSERT INTO quotes(origin,dest,miles,carrier,linehaul,fuel,accessorials,total,per_mile)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (parsed["origin"], parsed["dest"], parsed["miles"], parsed["carrier"],
         parsed["linehaul"], parsed["fuel"], parsed["accessorials"],
         parsed["total"], parsed["per_mile"]),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Ambient mode simulation (2036 vision)
# ---------------------------------------------------------------------------

def ambient_sim(origin, dest):
    conn = db()
    rows = conn.execute(
        "SELECT carrier, total, per_mile FROM quotes WHERE origin=? AND dest=? ORDER BY total",
        (origin, dest),
    ).fetchall()
    conn.close()
    carriers = [{"carrier": r["carrier"], "total": r["total"], "per_mile": r["per_mile"]}
                for r in rows]
    if not carriers:
        return {"lane": f"{origin} → {dest}", "carriers": [], "booked": None}
    booked = carriers[0]
    savings = round(max(c["total"] for c in carriers) - booked["total"], 2)
    return {
        "lane": f"{origin} → {dest}",
        "carriers": carriers,
        "booked": booked,
        "savings_vs_worst": savings,
    }


# ---------------------------------------------------------------------------
# Book & tender — create a load from the winning carrier/quote
# ---------------------------------------------------------------------------

import random


def gen_load_number():
    """Generate a PRO/load number: FN-<yymmdd>-<4 rand digits>."""
    from datetime import datetime
    stamp = datetime.now().strftime("%y%m%d")
    return f"FN-{stamp}-{random.randint(1000, 9999)}"


def create_booking(data):
    """Book a load. Accepts either {quote:{...}} or flat carrier+lane+rate fields,
    plus an optional sell rate to capture margin. Persists to bookings table."""
    q = data.get("quote") if isinstance(data.get("quote"), dict) else data
    # normalize inbound into the standard schema
    booked = coerce_schema({
        "origin": q.get("origin"),
        "dest": q.get("dest") or q.get("destination"),
        "miles": q.get("miles"),
        "carrier": q.get("carrier"),
        "linehaul": q.get("linehaul"),
        "fuel": q.get("fuel"),
        "accessorials": q.get("accessorials"),
        "total": q.get("total") or data.get("rate"),
        "accessorial_items": q.get("accessorial_items"),
    })
    sell = _num(data.get("sell"), 0.0)
    margin = round(sell - booked["total"], 2) if sell else 0.0
    margin_pct = round(margin / sell * 100, 1) if sell else 0.0
    load_number = gen_load_number()
    acc_json = json.dumps(booked.get("accessorial_items")) if booked.get("accessorial_items") else None

    conn = db()
    cur = conn.execute(
        """INSERT INTO bookings(load_number,origin,dest,miles,carrier,linehaul,fuel,
              accessorials,total,per_mile,sell,margin,margin_pct,status,accessorial_items)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (load_number, booked["origin"], booked["dest"], booked["miles"], booked["carrier"],
         booked["linehaul"], booked["fuel"], booked["accessorials"], booked["total"],
         booked["per_mile"], sell, margin, margin_pct, "Tendered", acc_json),
    )
    conn.commit()
    bid = cur.lastrowid
    conn.close()
    return {
        "id": bid, "load_number": load_number, "status": "Tendered",
        "origin": booked["origin"], "dest": booked["dest"], "miles": booked["miles"],
        "carrier": booked["carrier"], "linehaul": booked["linehaul"], "fuel": booked["fuel"],
        "accessorials": booked["accessorials"], "total": booked["total"],
        "per_mile": booked["per_mile"], "sell": sell, "margin": margin,
        "margin_pct": margin_pct,
        "accessorial_items": booked.get("accessorial_items", []),
        "rate_con_url": f"/rate-con/{bid}",
    }


def list_bookings():
    conn = db()
    rows = conn.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        items = []
        if r["accessorial_items"]:
            try:
                items = json.loads(r["accessorial_items"])
            except (ValueError, TypeError):
                items = []
        out.append({
            "id": r["id"], "load_number": r["load_number"], "status": r["status"],
            "origin": r["origin"], "dest": r["dest"], "miles": r["miles"],
            "carrier": r["carrier"], "linehaul": r["linehaul"], "fuel": r["fuel"],
            "accessorials": r["accessorials"], "total": r["total"],
            "per_mile": r["per_mile"], "sell": r["sell"], "margin": r["margin"],
            "margin_pct": r["margin_pct"], "accessorial_items": items,
            "created": r["created"], "rate_con_url": f"/rate-con/{r['id']}",
        })
    return out


def get_booking(bid):
    conn = db()
    r = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    conn.close()
    if not r:
        return None
    items = []
    if r["accessorial_items"]:
        try:
            items = json.loads(r["accessorial_items"])
        except (ValueError, TypeError):
            items = []
    d = dict(r)
    d["accessorial_items"] = items
    return d


def _h(s):
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rate_con_html(b):
    """Render a print-ready Rate Confirmation / load tender document."""
    money = lambda n: "${:,.2f}".format(float(n or 0))
    acc_rows = ""
    for a in (b.get("accessorial_items") or []):
        acc_rows += (f"<tr><td>{_h(a.get('label', 'Accessorial'))}</td>"
                     f"<td class='r'>{money(a.get('amount'))}</td></tr>")
    if not acc_rows and b.get("accessorials"):
        acc_rows = f"<tr><td>Accessorials</td><td class='r'>{money(b['accessorials'])}</td></tr>"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<title>Rate Confirmation {_h(b['load_number'])}</title>
<style>
:root{{--cyan:#0891b2;--ink:#0b1220;--slate:#5b7093;--line:#d4dde8;}}
*{{box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;color:var(--ink);background:#eef3f8;margin:0;padding:28px}}
.doc{{max-width:840px;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:10px;
  box-shadow:0 8px 40px rgba(11,18,32,.12);overflow:hidden}}
.head{{display:flex;justify-content:space-between;align-items:center;padding:26px 34px;
  background:linear-gradient(135deg,#0b1220,#0f2130);color:#eaf3fb}}
.head .logo{{width:40px;height:40px;border-radius:11px;background:conic-gradient(from 200deg,#22d3ee,#0e7490,#22d3ee);
  box-shadow:0 0 22px rgba(34,211,238,.5);float:left;margin-right:14px}}
.head h1{{margin:0;font-size:20px;letter-spacing:.5px}}
.head h1 span{{color:#22d3ee}}
.head .sub{{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#8aa0bd;margin-top:3px}}
.head .rc{{text-align:right}}
.head .rc .t{{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#8aa0bd}}
.head .rc .n{{font-size:22px;font-weight:800;color:#22d3ee;font-family:ui-monospace,monospace}}
.head .rc .st{{display:inline-block;margin-top:6px;font-size:11px;font-weight:700;padding:3px 11px;border-radius:20px;
  background:rgba(34,211,238,.18);color:#22d3ee}}
.body{{padding:30px 34px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
.box{{border:1px solid var(--line);border-radius:9px;padding:14px 16px}}
.box .k{{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--slate)}}
.box .v{{font-size:16px;font-weight:700;margin-top:3px}}
.box .v.big{{font-size:19px;color:var(--cyan)}}
h2{{font-size:12px;letter-spacing:1.6px;text-transform:uppercase;color:var(--slate);border-bottom:2px solid var(--cyan);
  padding-bottom:6px;margin:24px 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td{{padding:9px 10px;border-bottom:1px solid var(--line)}}
td.r{{text-align:right;font-family:ui-monospace,monospace}}
tr.tot td{{font-weight:800;font-size:16px;border-bottom:none;border-top:2px solid var(--ink)}}
tr.tot td.r{{color:var(--cyan)}}
.terms{{font-size:11px;color:var(--slate);line-height:1.7;margin-top:8px}}
.sign{{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-top:36px}}
.sign .line{{border-top:1px solid var(--ink);padding-top:6px;font-size:11px;color:var(--slate)}}
.foot{{text-align:center;font-size:10px;color:var(--slate);padding:16px;border-top:1px solid var(--line)}}
.actions{{max-width:840px;margin:0 auto 14px;text-align:right}}
.actions button{{cursor:pointer;border:none;border-radius:8px;padding:9px 18px;font-weight:700;font-size:12px;
  background:linear-gradient(160deg,#22d3ee,#0e7490);color:#04121a}}
@media print{{body{{background:#fff;padding:0}}.actions{{display:none}}.doc{{border:none;box-shadow:none}}}}
</style></head><body>
<div class="actions"><button onclick="window.print()">Print / Save PDF</button></div>
<div class="doc">
  <div class="head">
    <div><div class="logo"></div><h1>Freight<span>Normal</span></h1><div class="sub">Rate Confirmation / Load Tender</div></div>
    <div class="rc"><div class="t">Load #</div><div class="n">{_h(b['load_number'])}</div>
      <div class="st">{_h(b['status'])}</div></div>
  </div>
  <div class="body">
    <div class="grid">
      <div class="box"><div class="k">Carrier</div><div class="v">{_h(b['carrier'])}</div></div>
      <div class="box"><div class="k">Agreed Rate (All-in)</div><div class="v big">{money(b['total'])}</div></div>
      <div class="box"><div class="k">Origin</div><div class="v">{_h(b['origin'])}</div></div>
      <div class="box"><div class="k">Destination</div><div class="v">{_h(b['dest'])}</div></div>
      <div class="box"><div class="k">Miles</div><div class="v">{_h(b['miles'])} mi</div></div>
      <div class="box"><div class="k">Rate / Mile</div><div class="v">{money(b['per_mile'])}</div></div>
    </div>
    <h2>Rate Breakdown</h2>
    <table>
      <tr><td>Linehaul</td><td class="r">{money(b['linehaul'])}</td></tr>
      <tr><td>Fuel Surcharge</td><td class="r">{money(b['fuel'])}</td></tr>
      {acc_rows}
      <tr class="tot"><td>Total Agreed Rate</td><td class="r">{money(b['total'])}</td></tr>
    </table>
    <h2>Terms &amp; Conditions</h2>
    <div class="terms">
      1. This Rate Confirmation constitutes a binding tender under the master carrier agreement upon carrier acceptance.<br>
      2. Rate is all-inclusive of the linehaul, fuel, and accessorials itemized above; no additional charges without prior written authorization.<br>
      3. Carrier must dispatch a qualified driver and equipment and provide tracking updates per broker requirements.<br>
      4. Proof of Delivery (POD) and a legible invoice referencing Load #{_h(b['load_number'])} are required for payment.<br>
      5. Detention, layover, and other accessorials beyond those listed must be pre-approved and documented.
    </div>
    <div class="sign">
      <div class="line">Carrier signature &amp; date</div>
      <div class="line">Broker / FreightNormal representative</div>
    </div>
  </div>
  <div class="foot">FreightNormal &middot; Load {_h(b['load_number'])} &middot; Generated {_h(b.get('created', ''))} &middot; This is a system-generated tender document.</div>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Watchlist — saved lanes with corpus avg $/mile + last normalized quote
# ---------------------------------------------------------------------------

def add_watch(origin, dest):
    origin = (origin or "").strip()
    dest = (dest or "").strip()
    if not origin or not dest:
        return None
    conn = db()
    conn.execute("INSERT OR IGNORE INTO watchlist(origin,dest) VALUES(?,?)", (origin, dest))
    conn.commit()
    conn.close()
    return {"origin": origin, "dest": dest}


def get_watchlist():
    conn = db()
    watches = conn.execute("SELECT * FROM watchlist ORDER BY id DESC").fetchall()
    out = []
    for w in watches:
        agg = conn.execute(
            "SELECT AVG(per_mile) apm, AVG(total) at, COUNT(*) n FROM quotes WHERE origin=? AND dest=?",
            (w["origin"], w["dest"]),
        ).fetchone()
        last = conn.execute(
            "SELECT carrier, total, per_mile, created FROM quotes WHERE origin=? AND dest=? ORDER BY id DESC LIMIT 1",
            (w["origin"], w["dest"]),
        ).fetchone()
        out.append({
            "id": w["id"], "origin": w["origin"], "dest": w["dest"],
            "lane": f"{w['origin']} → {w['dest']}",
            "n": agg["n"] if agg else 0,
            "avg_per_mile": round(agg["apm"], 3) if agg and agg["apm"] else 0.0,
            "avg_total": round(agg["at"], 2) if agg and agg["at"] else 0.0,
            "last_quote": ({
                "carrier": last["carrier"], "total": round(last["total"], 2),
                "per_mile": round(last["per_mile"], 3), "created": last["created"],
            } if last else None),
        })
    conn.close()
    return out


# ---------------------------------------------------------------------------
# Autopilot — hands-free auto-book the best carrier when margin is good
# ---------------------------------------------------------------------------

AUTOPILOT_RATIONALE_PROMPT = (
    "You are an autonomous freight brokerage booking agent. In ONE short sentence "
    "(max 22 words, no preamble), give the rationale for this decision. "
    "Decision: {action}. Lane: {lane}. Carrier: {carrier}. Carrier cost: ${rate}. "
    "Target sell: ${sell}. Margin: {margin_pct}% (floor {floor}%). "
    "Respond with ONLY the sentence."
)


def claude_rationale(action, lane, carrier, rate, sell, margin_pct):
    """One-line booking rationale from Claude; mock fallback when no key/error."""
    prompt = AUTOPILOT_RATIONALE_PROMPT.format(
        action=action, lane=lane, carrier=carrier, rate=rate, sell=sell,
        margin_pct=margin_pct, floor=AUTOPILOT_MARGIN_THRESHOLD)
    if API_KEY:
        try:
            body = json.dumps({
                "model": CLAUDE_MODEL,
                "max_tokens": 80,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
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
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode())
            txt = "".join(b.get("text", "") for b in payload.get("content", [])
                          if b.get("type") == "text").strip()
            if txt:
                return txt.split("\n")[0].strip()
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError,
                json.JSONDecodeError, TimeoutError):
            pass
    # mock fallback
    if action == "booked":
        return (f"Cheapest carrier {carrier} clears the {AUTOPILOT_MARGIN_THRESHOLD:.0f}% "
                f"floor at {margin_pct}% margin — auto-booked on {lane}.")
    return (f"Best quote on {lane} yields only {margin_pct}% vs the "
            f"{AUTOPILOT_MARGIN_THRESHOLD:.0f}% floor — held, margin too thin.")


def best_carrier_for_lane(origin, dest):
    """Cheapest available carrier quote for a lane, from the corpus."""
    conn = db()
    row = conn.execute(
        """SELECT origin,dest,miles,carrier,linehaul,fuel,accessorials,total,per_mile
           FROM quotes WHERE origin=? AND dest=? ORDER BY total ASC LIMIT 1""",
        (origin, dest),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def log_autopilot(lane, carrier, rate, margin_pct, action, load_number, rationale):
    conn = db()
    conn.execute(
        """INSERT INTO autopilot_log(lane,carrier,rate,margin_pct,action,load_number,rationale)
           VALUES(?,?,?,?,?,?,?)""",
        (lane, carrier, rate, margin_pct, action, load_number, rationale),
    )
    conn.commit()
    conn.close()


def autopilot_step():
    """Run one autopilot cycle: take the next open load, evaluate the cheapest
    carrier, auto-book if margin >= threshold, else hold. Returns a feed event."""
    conn = db()
    load = conn.execute(
        "SELECT * FROM open_loads WHERE status='open' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    if not load:
        return {"action": "idle", "message": "queue empty — no open loads", "queue_remaining": 0}

    origin, dest, sell = load["origin"], load["dest"], load["target_sell"]
    lane = f"{origin} → {dest}"
    best = best_carrier_for_lane(origin, dest)

    if not best:
        # no carrier quotes for this lane — hold
        rationale = claude_rationale("held", lane, "(none)", 0, sell, 0.0)
        log_autopilot(lane, "(none)", 0.0, 0.0, "held", None, rationale)
        _mark_open_load(load["id"], "held", None)
        return {"action": "held", "lane": lane, "carrier": None, "rate": 0.0,
                "margin_pct": 0.0, "rationale": rationale, "load_number": None,
                "queue_remaining": _queue_remaining()}

    rate = best["total"]
    margin = round(sell - rate, 2)
    margin_pct = round(margin / sell * 100, 1) if sell else 0.0

    if margin_pct >= AUTOPILOT_MARGIN_THRESHOLD:
        booking = create_booking({"quote": best, "sell": sell})
        rationale = claude_rationale("booked", lane, best["carrier"], rate, sell, margin_pct)
        log_autopilot(lane, best["carrier"], rate, margin_pct, "booked",
                      booking["load_number"], rationale)
        _mark_open_load(load["id"], "booked", booking["load_number"])
        return {"action": "booked", "lane": lane, "carrier": best["carrier"],
                "rate": rate, "sell": sell, "margin": margin, "margin_pct": margin_pct,
                "rationale": rationale, "load_number": booking["load_number"],
                "rate_con_url": booking["rate_con_url"],
                "queue_remaining": _queue_remaining()}

    rationale = claude_rationale("held", lane, best["carrier"], rate, sell, margin_pct)
    log_autopilot(lane, best["carrier"], rate, margin_pct, "held", None, rationale)
    _mark_open_load(load["id"], "held", None)
    return {"action": "held", "lane": lane, "carrier": best["carrier"], "rate": rate,
            "sell": sell, "margin": margin, "margin_pct": margin_pct,
            "rationale": rationale, "load_number": None,
            "queue_remaining": _queue_remaining()}


def _mark_open_load(load_id, status, load_number):
    conn = db()
    conn.execute("UPDATE open_loads SET status=?, load_number=? WHERE id=?",
                 (status, load_number, load_id))
    conn.commit()
    conn.close()


def _queue_remaining():
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM open_loads WHERE status='open'").fetchone()[0]
    conn.close()
    return n


def autopilot_log(limit=100):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM autopilot_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("load_number"):
            d["rate_con_url"] = None  # filled below if we can resolve booking id
        out.append(d)
    # attach rate-con links for booked rows
    conn = db()
    for d in out:
        if d.get("load_number"):
            b = conn.execute("SELECT id FROM bookings WHERE load_number=? LIMIT 1",
                             (d["load_number"],)).fetchone()
            if b:
                d["rate_con_url"] = f"/rate-con/{b['id']}"
    conn.close()
    return out


def autopilot_state():
    conn = db()
    booked = conn.execute("SELECT COUNT(*) c FROM open_loads WHERE status='booked'").fetchone()[0]
    held = conn.execute("SELECT COUNT(*) c FROM open_loads WHERE status='held'").fetchone()[0]
    remaining = conn.execute("SELECT COUNT(*) c FROM open_loads WHERE status='open'").fetchone()[0]
    conn.close()
    return {"running": remaining > 0, "booked_count": booked,
            "held_count": held, "queue_remaining": remaining}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

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
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, {"error": "index.html missing"})
            return
        if self.path == "/api/health":
            self._send(200, {"ok": True, "engine": "claude" if API_KEY else "mock",
                             "model": CLAUDE_MODEL if API_KEY else None})
            return
        if self.path == "/api/lanes":
            self._send(200, {"lanes": lane_intel()})
            return
        if self.path == "/api/bookings":
            self._send(200, {"bookings": list_bookings()})
            return
        if self.path == "/api/watchlist":
            self._send(200, {"watchlist": get_watchlist()})
            return
        if self.path == "/api/autopilot/log":
            self._send(200, {"log": autopilot_log()})
            return
        if self.path == "/api/autopilot/state":
            self._send(200, autopilot_state())
            return
        if self.path.startswith("/rate-con/"):
            tail = self.path[len("/rate-con/"):].split("?")[0].split("/")[0]
            try:
                bid = int(tail)
            except ValueError:
                self._send(404, {"error": "bad load id"})
                return
            b = get_booking(bid)
            if not b:
                self._send(404, {"error": "load not found"})
                return
            self._send(200, rate_con_html(b), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/ambient"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            lanes = lane_intel()
            if lanes:
                default = lanes[0]
                origin = q.get("origin", [default["origin"]])[0]
                dest = q.get("dest", [default["dest"]])[0]
            else:
                origin = q.get("origin", [""])[0]
                dest = q.get("dest", [""])[0]
            self._send(200, ambient_sim(origin, dest))
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/normalize":
            data = self._read_json()
            text = data.get("text", "")
            if not text.strip():
                self._send(400, {"error": "empty quote text"})
                return
            parsed = normalize(text)
            verdict = market_verdict(parsed)
            if data.get("save", True):
                persist_quote(parsed)
            self._send(200, {"normalized": parsed, "market": verdict})
            return
        if self.path == "/api/book":
            data = self._read_json()
            src = data.get("quote") if isinstance(data.get("quote"), dict) else data
            if not (src.get("carrier") or src.get("origin")):
                self._send(400, {"error": "need a quote or carrier+lane+rate to book"})
                return
            booking = create_booking(data)
            self._send(200, {"booking": booking})
            return
        if self.path == "/api/autopilot/step":
            self._send(200, {"event": autopilot_step(), "state": autopilot_state()})
            return
        if self.path == "/api/watch":
            data = self._read_json()
            w = add_watch(data.get("origin"), data.get("dest"))
            if not w:
                self._send(400, {"error": "origin and dest required"})
                return
            self._send(200, {"watched": w, "watchlist": get_watchlist()})
            return
        self._send(404, {"error": "not found"})


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    engine = f"claude ({CLAUDE_MODEL})" if API_KEY else "mock-heuristic (offline)"
    print(f"FreightNormal on http://localhost:{PORT}  | normalize engine: {engine}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
