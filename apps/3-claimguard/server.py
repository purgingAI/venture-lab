#!/usr/bin/env python3
"""
ClaimGuard - pre-submission medical billing-error catcher.
Python 3 stdlib only. No external deps.

Serves a single-file index.html and a small JSON API on PORT 8803.
SQLite is seeded on first run with mock claims + a denial-rules table.

If /home/sagar-uniyal/creator-os/.env has ANTHROPIC_API_KEY, each flag's
plain-English explanation is generated with a real Claude call (urllib),
otherwise a deterministic MOCK explanation is used.
"""

import datetime
import json
import os
import re
import sqlite3
import ssl
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "claimguard.db")
INDEX_PATH = os.path.join(HERE, "index.html")
PORT = 8803

ENV_PATH = "/home/sagar-uniyal/creator-os/.env"
CLAUDE_MODEL = "claude-sonnet-4-6"
_db_lock = threading.Lock()


# --------------------------------------------------------------------------
# Anthropic key / Claude call
# --------------------------------------------------------------------------
def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


API_KEY = load_api_key()


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def claude_explain(rule_name, claim, rule_detail):
    """Return a plain-English fix for a flag. Real Claude if key present, else MOCK."""
    if not API_KEY:
        return mock_explain(rule_name, claim, rule_detail)
    prompt = (
        "You are a medical-billing coding expert. In 1-2 short sentences, plainly "
        "explain to a small-practice biller why this claim would likely be DENIED and "
        "the concrete fix. Be specific and actionable. No preamble.\n\n"
        f"Claim: CPT {claim.get('cpt')} / ICD {claim.get('icd')} / payer "
        f"{claim.get('payer')} / ${claim.get('amount')} / modifier "
        f"{claim.get('modifier') or 'none'} / auth {claim.get('auth_status') or 'n/a'}.\n"
        f"Denial rule hit: {rule_name} - {rule_detail}"
    )
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 160,
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
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            data = json.loads(r.read().decode())
        parts = data.get("content", [])
        txt = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return txt.strip() or mock_explain(rule_name, claim, rule_detail)
    except Exception:
        return mock_explain(rule_name, claim, rule_detail)


def mock_explain(rule_name, claim, rule_detail):
    templates = {
        "MISSING_MODIFIER": (
            f"Payer {claim.get('payer')} bundles CPT {claim.get('cpt')} with a related "
            "service, so it needs a distinct-procedure modifier (e.g. -59 or -25). "
            "Add the correct modifier to unbundle and get paid."
        ),
        "ICD_CPT_MISMATCH": (
            f"ICD {claim.get('icd')} does not medically justify CPT {claim.get('cpt')} for "
            f"{claim.get('payer')}. Recode to a diagnosis that supports the procedure or "
            "the line will be denied for lack of medical necessity."
        ),
        "PAYER_CAP_EXCEEDED": (
            f"{claim.get('payer')} caps this CPT below the ${claim.get('amount')} billed. "
            "Rebill at the contracted allowed amount to avoid an automatic reduction/denial."
        ),
        "STALE_AUTH": (
            "The prior authorization is expired or missing for this service. Obtain/refresh "
            f"the auth with {claim.get('payer')} before submitting or it will be hard-denied."
        ),
        "DUPLICATE_CLAIM": (
            "This CPT/date/patient combination looks like a duplicate already on file. "
            "Confirm it is not a resubmission, or append the correct resubmission code."
        ),
        "TELEHEALTH_POS": (
            "Telehealth service is missing place-of-service 02/10 and the -95 modifier that "
            f"{claim.get('payer')} requires. Correct POS + modifier to avoid denial."
        ),
    }
    return templates.get(rule_name, rule_detail)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


DENIAL_RULES = [
    ("MISSING_MODIFIER", "Missing / incorrect procedure modifier",
     "Bundled CPT submitted without a distinct-service modifier (-59/-25).", 0.34),
    ("ICD_CPT_MISMATCH", "ICD / CPT medical-necessity mismatch",
     "Diagnosis code does not support the billed procedure.", 0.28),
    ("PAYER_CAP_EXCEEDED", "Payer-specific fee cap exceeded",
     "Billed amount exceeds the payer's contracted allowed amount.", 0.16),
    ("STALE_AUTH", "Stale / missing prior authorization",
     "Authorization expired or absent for a service that requires it.", 0.14),
    ("DUPLICATE_CLAIM", "Duplicate claim",
     "Same CPT / date / patient already on file.", 0.05),
    ("TELEHEALTH_POS", "Telehealth POS / modifier error",
     "Telehealth service missing POS 02/10 or -95 modifier.", 0.03),
]

# 15 seeded mock claims. Some clean, most with a plantable issue.
SEED_CLAIMS = [
    # patient, cpt, icd, payer, amount, modifier, auth_status, pos, telehealth
    ("Alvarez, M.",  "99213", "E11.9",  "Aetna",      120.00, "",    "active",  "11", 0),
    ("Brooks, T.",   "20610", "M17.11", "UnitedHealth",210.00, "",    "active",  "11", 0),  # missing -RT/-59
    ("Chen, L.",     "99214", "Z00.00", "Cigna",      185.00, "",    "active",  "11", 0),  # ICD/CPT mismatch
    ("Diaz, R.",     "93000", "R07.9",  "Medicare",   95.00,  "",    "active",  "11", 0),
    ("Evans, K.",    "97110", "M54.5",  "Aetna",      680.00, "",    "active",  "11", 0),  # payer cap
    ("Ford, S.",     "99215", "I10",    "BlueCross",  240.00, "",    "expired", "11", 0),  # stale auth
    ("Gupta, A.",    "36415", "Z01.812","UnitedHealth",22.00, "",    "active",  "11", 0),
    ("Hughes, P.",   "90837", "F41.1",  "Cigna",      160.00, "",    "active",  "02", 1),  # telehealth POS
    ("Ito, N.",      "99213", "E11.9",  "Aetna",      120.00, "",    "active",  "11", 0),  # duplicate of #1
    ("Jones, D.",    "11042", "L97.909","Medicare",   340.00, "-59", "active",  "11", 0),
    ("Kaur, H.",     "99204", "J45.909","BlueCross",  260.00, "",    "active",  "11", 0),
    ("Lopez, V.",    "20610", "M17.12", "Cigna",      205.00, "",    "missing", "11", 0),  # stale auth
    ("Miller, J.",   "99213", "I10",    "UnitedHealth",130.00,"-25", "active",  "11", 0),
    ("Nguyen, B.",   "97140", "M25.511","Aetna",      560.00, "",    "active",  "11", 0),  # payer cap
    ("Owens, C.",    "99214", "Z71.3",  "Medicare",   185.00, "",    "active",  "11", 0),  # ICD/CPT mismatch
]

# CPT->ICD medical-necessity whitelist (very small mock knowledge base).
CPT_ICD_OK = {
    "99213": {"E11.9", "I10", "J45.909", "M54.5"},
    "99214": {"E11.9", "I10", "M54.5", "J45.909"},
    "99215": {"I10", "E11.9"},
    "99204": {"J45.909", "I10"},
    "93000": {"R07.9", "I10", "R00.2"},
    "20610": {"M17.11", "M17.12", "M25.561"},
    "97110": {"M54.5", "M25.561", "M62.81"},
    "97140": {"M25.511", "M54.5", "M62.81"},
    "90837": {"F41.1", "F33.1", "F43.10"},
}
# Payer contracted caps by CPT (mock).
PAYER_CAPS = {
    ("Aetna", "97110"): 65.00,
    ("Aetna", "97140"): 70.00,
    ("Cigna", "99214"): 175.00,
    ("Medicare", "99214"): 160.00,
    ("BlueCross", "99204"): 300.00,
}


# ~12 sample claims for the batch-scan "load sample" button. Mix of clean + issues.
SAMPLE_BATCH = [
    # patient, cpt, icd, payer, amount, modifier, auth_status, pos, telehealth
    ("Aguilar, R.", "20610", "M17.11", "UnitedHealth", 210.00, "",    "active",  "11", 0),
    ("Bennett, S.", "99213", "E11.9",  "Aetna",        120.00, "",    "active",  "11", 0),
    ("Carter, M.",  "99214", "Z00.00", "Cigna",        185.00, "",    "active",  "11", 0),
    ("Delgado, F.", "97110", "M54.5",  "Aetna",        680.00, "",    "active",  "11", 0),
    ("Ellis, T.",   "99215", "I10",    "BlueCross",    240.00, "",    "expired", "11", 0),
    ("Frost, L.",   "90837", "F41.1",  "Cigna",        160.00, "",    "active",  "02", 1),
    ("Gomez, A.",   "36415", "Z01.812","UnitedHealth", 22.00,  "",    "active",  "11", 0),
    ("Hale, N.",    "97140", "M25.511","Aetna",        560.00, "",    "active",  "11", 0),
    ("Irwin, P.",   "20610", "M17.12", "Cigna",        205.00, "",    "missing", "11", 0),
    ("Jansen, D.",  "11042", "L97.909","Medicare",     340.00, "-59", "active",  "11", 0),
    ("Kerr, V.",    "99204", "J45.909","BlueCross",    260.00, "",    "active",  "11", 0),
    ("Long, C.",    "99214", "Z71.3",  "Medicare",     185.00, "",    "active",  "11", 0),
]

# 6 fixed columns the CSV parser understands (order-independent via header row).
BATCH_COLS = ["patient", "cpt", "icd", "payer", "amount", "modifier", "auth_status", "pos", "telehealth"]


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS denial_rules(
        code TEXT PRIMARY KEY, name TEXT, detail TEXT, base_rate REAL, hits INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS claims(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient TEXT, cpt TEXT, icd TEXT, payer TEXT, amount REAL,
        modifier TEXT, auth_status TEXT, pos TEXT, telehealth INTEGER,
        status TEXT DEFAULT 'New', risk REAL DEFAULT 0, at_risk REAL DEFAULT 0,
        flags TEXT DEFAULT '[]')""")
    # Additive migration: autopilot activity log (safe on existing DBs).
    c.execute("""CREATE TABLE IF NOT EXISTS autopilot_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, claim INTEGER, patient TEXT, action TEXT,
        recovered REAL DEFAULT 0, rationale TEXT)""")
    if fresh:
        for r in DENIAL_RULES:
            c.execute("INSERT INTO denial_rules(code,name,detail,base_rate) VALUES(?,?,?,?)", r)
        for s in SEED_CLAIMS:
            c.execute("""INSERT INTO claims(patient,cpt,icd,payer,amount,modifier,auth_status,pos,telehealth)
                         VALUES(?,?,?,?,?,?,?,?,?)""", s)
        conn.commit()
        # score every seeded claim so the dashboard is populated.
        # fast=True -> MOCK explanations for a snappy first boot (no 15x API calls).
        seen = {}
        for row in c.execute("SELECT * FROM claims ORDER BY id").fetchall():
            claim = dict(row)
            res = analyze(claim, seen_signatures=dict(seen), fast=True)
            persist_scan(conn, claim["id"], res)
            seen[(claim["cpt"], claim["payer"], round(float(claim["amount"]), 2))] = True
        conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Scan / rules engine
# --------------------------------------------------------------------------
def analyze(claim, seen_signatures, fast=False):
    """Run the denial-pattern ruleset over a claim dict. Returns result dict.
    fast=True uses MOCK explanations only (used for fast first-run seeding)."""
    flags = []
    cpt = str(claim.get("cpt", "")).strip()
    icd = str(claim.get("icd", "")).strip().upper()
    payer = str(claim.get("payer", "")).strip()
    amount = float(claim.get("amount") or 0)
    modifier = str(claim.get("modifier") or "").strip()
    auth = str(claim.get("auth_status") or "active").strip().lower()
    telehealth = int(claim.get("telehealth") or 0)
    pos = str(claim.get("pos") or "11").strip()

    # Rule: bundled CPTs that need a distinct-service modifier.
    bundled = {"20610", "97140", "11042"}
    if cpt in bundled and modifier == "":
        flags.append(rule_hit("MISSING_MODIFIER", claim, amount * 1.0, fast))

    # Rule: ICD/CPT medical-necessity mismatch.
    allowed = CPT_ICD_OK.get(cpt)
    if allowed is not None and icd not in allowed:
        flags.append(rule_hit("ICD_CPT_MISMATCH", claim, amount * 1.0, fast))

    # Rule: payer-specific fee cap exceeded.
    cap = PAYER_CAPS.get((payer, cpt))
    if cap is not None and amount > cap:
        flags.append(rule_hit("PAYER_CAP_EXCEEDED", claim, amount - cap, fast))

    # Rule: stale / missing authorization.
    if auth in ("expired", "missing", "stale", "none", ""):
        if auth in ("expired", "missing", "stale"):
            flags.append(rule_hit("STALE_AUTH", claim, amount * 1.0, fast))

    # Rule: telehealth POS / modifier.
    if telehealth and (pos not in ("02", "10") or "-95" not in modifier):
        flags.append(rule_hit("TELEHEALTH_POS", claim, amount * 1.0, fast))

    # Rule: duplicate (same cpt/patient/payer/amount signature already seen).
    if seen_signatures is not None:
        sig = (claim.get("patient", "").split(",")[0].strip().lower(), cpt, payer)
        # duplicate keyed on cpt+payer+amount regardless of patient surname collision
        dupkey = (cpt, payer, round(amount, 2))
        if dupkey in seen_signatures:
            flags.append(rule_hit("DUPLICATE_CLAIM", claim, amount * 1.0, fast))

    at_risk = round(sum(f["at_risk"] for f in flags), 2)
    # Risk score: combine rule base rates, capped at 98.
    if flags:
        combined = 1.0
        for f in flags:
            combined *= (1 - min(f["base_rate"] * 1.6, 0.9))
        risk = int(round((1 - combined) * 100))
        risk = max(35, min(98, risk))
    else:
        risk = 4
    status = "Clean" if not flags else "At-Risk"
    return {"flags": flags, "at_risk": at_risk, "risk": risk, "status": status}


RULE_BY_CODE = {r[0]: r for r in DENIAL_RULES}


def rule_hit(code, claim, at_risk, fast=False):
    r = RULE_BY_CODE[code]
    explain = mock_explain if fast else claude_explain
    return {
        "code": code,
        "name": r[1],
        "detail": r[2],
        "base_rate": r[3],
        "at_risk": round(max(at_risk, 0), 2),
        "fix": explain(code, claim, r[2]),
    }


def persist_scan(conn, claim_id, res):
    conn.execute(
        "UPDATE claims SET status=?, risk=?, at_risk=?, flags=? WHERE id=?",
        (res["status"], res["risk"], res["at_risk"], json.dumps(res["flags"]), claim_id),
    )
    for f in res["flags"]:
        conn.execute("UPDATE denial_rules SET hits=hits+1 WHERE code=?", (f["code"],))


def existing_signatures(conn, exclude_id=None):
    sigs = {}
    q = "SELECT id,cpt,payer,amount FROM claims"
    for row in conn.execute(q).fetchall():
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        sigs[(row["cpt"], row["payer"], round(row["amount"], 2))] = True
    return sigs


# --------------------------------------------------------------------------
# API handlers
# --------------------------------------------------------------------------
def api_scan(payload):
    """Score an ad-hoc or existing claim. If 'id' present, rescan+persist it."""
    with _db_lock:
        conn = db()
        try:
            if payload.get("id"):
                cid = int(payload["id"])
                row = conn.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
                if not row:
                    return {"error": "claim not found"}, 404
                claim = dict(row)
                sigs = existing_signatures(conn, exclude_id=cid)
                res = analyze(claim, sigs)
                persist_scan(conn, cid, res)
                conn.commit()
                claim.update({"id": cid})
                return {"claim": claim, **res}, 200

            # New ad-hoc claim -> insert then score.
            claim = {
                "patient": payload.get("patient") or "Ad-hoc, Q.",
                "cpt": str(payload.get("cpt", "")).strip(),
                "icd": str(payload.get("icd", "")).strip().upper(),
                "payer": str(payload.get("payer", "")).strip(),
                "amount": float(payload.get("amount") or 0),
                "modifier": str(payload.get("modifier") or "").strip(),
                "auth_status": str(payload.get("auth_status") or "active").strip(),
                "pos": str(payload.get("pos") or "11").strip(),
                "telehealth": 1 if payload.get("telehealth") in (1, "1", True, "true", "on") else 0,
            }
            cur = conn.execute(
                """INSERT INTO claims(patient,cpt,icd,payer,amount,modifier,auth_status,pos,telehealth)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (claim["patient"], claim["cpt"], claim["icd"], claim["payer"], claim["amount"],
                 claim["modifier"], claim["auth_status"], claim["pos"], claim["telehealth"]),
            )
            cid = cur.lastrowid
            sigs = existing_signatures(conn, exclude_id=cid)
            res = analyze(claim, sigs)
            persist_scan(conn, cid, res)
            conn.commit()
            claim["id"] = cid
            return {"claim": claim, **res}, 200
        finally:
            conn.close()


def api_fix(payload):
    """Mark a claim Fixed -> revenue protected."""
    with _db_lock:
        conn = db()
        try:
            cid = int(payload.get("id"))
            row = conn.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
            if not row:
                return {"error": "not found"}, 404
            conn.execute("UPDATE claims SET status='Fixed' WHERE id=?", (cid,))
            conn.commit()
            return {"ok": True, "id": cid, "protected": row["at_risk"]}, 200
        finally:
            conn.close()


def api_dashboard():
    conn = db()
    try:
        claims = [dict(r) for r in conn.execute(
            "SELECT id,patient,cpt,icd,payer,amount,status,risk,at_risk,flags FROM claims ORDER BY id DESC").fetchall()]
        for c in claims:
            try:
                c["flags"] = json.loads(c["flags"] or "[]")
            except Exception:
                c["flags"] = []
        total = len(claims)
        clean = sum(1 for c in claims if c["status"] == "Clean")
        at_risk_n = sum(1 for c in claims if c["status"] == "At-Risk")
        fixed = sum(1 for c in claims if c["status"] == "Fixed")
        protected = round(sum(c["at_risk"] for c in claims if c["status"] == "Fixed"), 2)
        exposure = round(sum(c["at_risk"] for c in claims if c["status"] == "At-Risk"), 2)
        rules = [dict(r) for r in conn.execute(
            "SELECT code,name,detail,base_rate,hits FROM denial_rules ORDER BY hits DESC, base_rate DESC").fetchall()]
        return {
            "claims": claims,
            "stats": {
                "total": total, "clean": clean, "at_risk": at_risk_n, "fixed": fixed,
                "protected": protected, "exposure": exposure,
            },
            "rules": rules,
            "ai": bool(API_KEY),
        }, 200
    finally:
        conn.close()


def _parse_batch_csv(text):
    """Parse a pasted CSV into a list of claim dicts. First non-empty line is the header.
    Unknown columns ignored; missing columns defaulted. Returns list of dicts."""
    import csv
    import io
    rows = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows
    reader = csv.reader(io.StringIO("\n".join(lines)))
    parsed = list(reader)
    if not parsed:
        return rows
    header = [h.strip().lower() for h in parsed[0]]
    # detect whether row 0 is a header or data (header must contain known col names)
    has_header = any(h in BATCH_COLS for h in header)
    if has_header:
        body = parsed[1:]
        cols = header
    else:
        body = parsed
        cols = BATCH_COLS[:len(parsed[0])]
    for raw in body:
        rec = {}
        for i, col in enumerate(cols):
            if col in BATCH_COLS and i < len(raw):
                rec[col] = raw[i].strip()
        if rec.get("cpt") or rec.get("patient"):
            rows.append(rec)
    return rows


def _normalize_batch_claim(rec):
    """Coerce a raw batch record (dict) into the canonical claim shape."""
    return {
        "patient": rec.get("patient") or "Batch, Q.",
        "cpt": str(rec.get("cpt", "")).strip(),
        "icd": str(rec.get("icd", "")).strip().upper(),
        "payer": str(rec.get("payer", "")).strip(),
        "amount": float(rec.get("amount") or 0) if str(rec.get("amount") or "").replace(".", "", 1).replace("-", "", 1).isdigit() else 0.0,
        "modifier": str(rec.get("modifier") or "").strip(),
        "auth_status": str(rec.get("auth_status") or "active").strip(),
        "pos": str(rec.get("pos") or "11").strip(),
        "telehealth": 1 if str(rec.get("telehealth") or "0").strip() in ("1", "true", "yes", "y", "on") else 0,
    }


def api_batch_scan(payload):
    """Score multiple claims at once (pasted CSV text OR a JSON array).
    Does NOT persist to the ledger — this is an analysis-only bulk pass.
    Returns per-claim results + an aggregate summary."""
    records = []
    if isinstance(payload.get("claims"), list):
        records = [dict(r) for r in payload["claims"] if isinstance(r, dict)]
    elif payload.get("csv"):
        records = _parse_batch_csv(str(payload["csv"]))
    elif payload.get("sample"):
        records = [dict(zip(BATCH_COLS, s)) for s in SAMPLE_BATCH]
    if not records:
        return {"error": "no claims provided", "results": [], "aggregate": {}}, 400

    results = []
    seen = {}
    reason_totals = {}
    payer_totals = {}
    total_at_risk = 0.0
    clean_n = 0
    at_risk_n = 0
    for idx, rec in enumerate(records, 1):
        claim = _normalize_batch_claim(rec)
        # batch uses fast (MOCK) explanations to stay snappy at scale.
        res = analyze(claim, seen_signatures=dict(seen), fast=True)
        seen[(claim["cpt"], claim["payer"], round(claim["amount"], 2))] = True
        total_at_risk += res["at_risk"]
        if res["status"] == "Clean":
            clean_n += 1
        else:
            at_risk_n += 1
        for f in res["flags"]:
            reason_totals[f["name"]] = reason_totals.get(f["name"], 0) + 1
            payer_totals[claim["payer"] or "Unknown"] = round(
                payer_totals.get(claim["payer"] or "Unknown", 0) + f["at_risk"], 2)
        results.append({
            "n": idx,
            "patient": claim["patient"], "cpt": claim["cpt"], "icd": claim["icd"],
            "payer": claim["payer"], "amount": round(claim["amount"], 2),
            "status": res["status"], "risk": res["risk"], "at_risk": res["at_risk"],
            "flags": res["flags"],
        })

    top_reasons = sorted(
        ({"name": k, "count": v} for k, v in reason_totals.items()),
        key=lambda x: x["count"], reverse=True)
    payer_breakdown = sorted(
        ({"payer": k, "at_risk": v} for k, v in payer_totals.items()),
        key=lambda x: x["at_risk"], reverse=True)
    total = len(results)
    aggregate = {
        "total": total,
        "clean": clean_n,
        "at_risk": at_risk_n,
        "clean_rate": round(clean_n / total * 100, 1) if total else 0.0,
        "total_at_risk": round(total_at_risk, 2),
        "top_reasons": top_reasons,
        "payer_breakdown": payer_breakdown,
    }
    return {"results": results, "aggregate": aggregate}, 200


def api_analytics():
    """Denial-trend analytics over the ledger + a seeded 12-week prevented-$ trend.
    Combines persisted ledger claims (real) with a deterministic seeded trend so the
    view looks alive on first run."""
    conn = db()
    try:
        claims = [dict(r) for r in conn.execute(
            "SELECT payer,amount,status,at_risk,flags FROM claims").fetchall()]
        rules = [dict(r) for r in conn.execute(
            "SELECT code,name,hits,base_rate FROM denial_rules ORDER BY hits DESC").fetchall()]
    finally:
        conn.close()

    payer_at_risk = {}
    reason_counts = {}
    total = len(claims)
    clean = 0
    for c in claims:
        if c["status"] == "Clean":
            clean += 1
        try:
            flags = json.loads(c["flags"] or "[]")
        except Exception:
            flags = []
        for f in flags:
            payer_at_risk[c["payer"] or "Unknown"] = round(
                payer_at_risk.get(c["payer"] or "Unknown", 0) + f.get("at_risk", 0), 2)
            reason_counts[f.get("name", "?")] = reason_counts.get(f.get("name", "?"), 0) + 1

    payer_breakdown = sorted(
        ({"payer": k, "at_risk": v} for k, v in payer_at_risk.items()),
        key=lambda x: x["at_risk"], reverse=True)
    # fall back to rule.hits if no flags persisted yet
    if reason_counts:
        top_reasons = sorted(
            ({"name": k, "count": v} for k, v in reason_counts.items()),
            key=lambda x: x["count"], reverse=True)
    else:
        top_reasons = [{"name": r["name"], "count": r["hits"]} for r in rules if r["hits"] > 0]

    clean_rate = round(clean / total * 100, 1) if total else 0.0

    # Seeded 12-week prevented-$ trend: deterministic pseudo-wave so it looks alive.
    import math
    weeks = []
    base = 1400
    for w in range(12):
        val = base + int(340 * math.sin(w / 1.7)) + (w * 165) + (w % 3) * 90
        weeks.append({
            "week": "W" + str(w + 1),
            "prevented": val,
            "denials": 14 + int(6 * math.sin(w / 2.1 + 1)) + (w % 4),
        })
    trend_total = sum(w["prevented"] for w in weeks)

    return {
        "payer_breakdown": payer_breakdown,
        "top_reasons": top_reasons,
        "clean_rate": clean_rate,
        "total_claims": total,
        "trend": weeks,
        "trend_total": trend_total,
    }, 200


def api_ambient():
    """Simulate the 2036 ambient agent auto-appealing a denied claim."""
    steps = [
        {"t": "Denial ingested", "d": "Payer 835 remit posted CO-16 (missing modifier) on claim #B-2210.", "ok": True},
        {"t": "Root cause matched", "d": "Denial-pattern engine matched MISSING_MODIFIER with 94% confidence.", "ok": True},
        {"t": "Corrected claim drafted", "d": "Appended -59, re-priced to contracted allowed, attached op note.", "ok": True},
        {"t": "Appeal letter authored", "d": "Claude drafted a payer-specific reconsideration letter citing CPT rules.", "ok": True},
        {"t": "Auto-submitted", "d": "837 corrected claim + appeal e-filed to payer portal. Tracking #AP-77120.", "ok": True},
        {"t": "Recovery projected", "d": "$182.00 expected to reprocess within 14 days. Human touch: 0 minutes.", "ok": True},
    ]
    return {"steps": steps, "recovered": 182.00, "human_minutes": 0}, 200


# --------------------------------------------------------------------------
# Autopilot — hands-free auto-appeal of SAFE, fixable at-risk claims
# --------------------------------------------------------------------------
# Denial codes the autopilot is allowed to auto-correct without a human. These
# are deterministic coding fixes (add a modifier, correct POS). Anything else
# (medical-necessity recoding, fee disputes, dup resolution, stale auth) is
# ambiguous or clinical -> queued for a biller.
SAFE_AUTOPILOT_CODES = {"MISSING_MODIFIER", "TELEHEALTH_POS"}
# Dollar ceiling above which even a "safe" flag is escalated to human review.
AUTOPILOT_HIGH_DOLLAR = 500.0


def claude_autopilot_draft(claim, flag):
    """Draft a concrete correction + one-line appeal rationale for a safe flag.
    Real Claude if key present, else deterministic MOCK. Returns (correction, rationale)."""
    code = flag.get("code")
    if not API_KEY:
        return mock_autopilot_draft(claim, flag)
    prompt = (
        "You are an autonomous medical-billing correction agent. A claim was flagged "
        "with a SAFE, mechanically-fixable coding denial. Respond ONLY with strict JSON: "
        '{"correction": "...", "rationale": "..."}. '
        "'correction' = the exact concrete edit to apply (e.g. 'Append modifier -59'). "
        "'rationale' = one short sentence justifying the appeal. No preamble, no markdown.\n\n"
        f"Claim: CPT {claim.get('cpt')} / ICD {claim.get('icd')} / payer "
        f"{claim.get('payer')} / ${claim.get('amount')} / modifier "
        f"{claim.get('modifier') or 'none'} / POS {claim.get('pos')}.\n"
        f"Flag: {flag.get('name')} - {flag.get('detail')}"
    )
    body = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 200,
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
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            data = json.loads(r.read().decode())
        parts = data.get("content", [])
        txt = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            obj = json.loads(m.group(0))
            corr = str(obj.get("correction", "")).strip()
            rat = str(obj.get("rationale", "")).strip()
            if corr and rat:
                return corr, rat
    except Exception:
        pass
    return mock_autopilot_draft(claim, flag)


def mock_autopilot_draft(claim, flag):
    code = flag.get("code")
    if code == "MISSING_MODIFIER":
        mod = "-59" if str(claim.get("cpt")) in ("20610", "11042") else "-25"
        return (
            f"Append distinct-service modifier {mod} to CPT {claim.get('cpt')} and resubmit.",
            f"CPT {claim.get('cpt')} is a distinct procedure; {mod} unbundles it per "
            f"{claim.get('payer')} policy, so the line is payable as billed.",
        )
    if code == "TELEHEALTH_POS":
        return (
            "Set POS to 02 and append modifier -95 to the telehealth line.",
            f"Service was rendered via real-time audio-video; POS 02 + -95 satisfies "
            f"{claim.get('payer')} telehealth requirements.",
        )
    return (
        "Apply the recommended coding correction and resubmit.",
        "Correction aligns the claim with payer adjudication rules.",
    )


def _autopilot_state(conn):
    fixed = conn.execute(
        "SELECT COUNT(*) FROM autopilot_log WHERE action='corrected'").fetchone()[0]
    needs = conn.execute(
        "SELECT COUNT(*) FROM autopilot_log WHERE action='queued'").fetchone()[0]
    recovered = conn.execute(
        "SELECT COALESCE(SUM(recovered),0) FROM autopilot_log WHERE action='corrected'").fetchone()[0]
    return {
        "fixed_count": fixed,
        "needs_review": needs,
        "recovered_total": round(recovered or 0, 2),
    }


def api_autopilot_step(payload):
    """Run ONE autonomous cycle: pick the next unhandled at-risk claim, decide, act.
    - SAFE + low-dollar fixable flag  -> auto-correct, mark Fixed, log 'corrected'.
    - high-dollar or ambiguous flag   -> log 'queued' for biller review (no auto-fix).
    Returns the step result plus rolling state. Idempotent per claim (skips claims
    already present in autopilot_log)."""
    with _db_lock:
        conn = db()
        try:
            # Next at-risk claim not yet touched by the autopilot.
            row = conn.execute(
                """SELECT * FROM claims
                   WHERE status='At-Risk'
                     AND id NOT IN (SELECT claim FROM autopilot_log)
                   ORDER BY id ASC LIMIT 1""").fetchone()
            if not row:
                st = _autopilot_state(conn)
                return {"done": True, "message": "No more at-risk claims to process.",
                        "state": st}, 200

            claim = dict(row)
            try:
                flags = json.loads(claim.get("flags") or "[]")
            except Exception:
                flags = []

            safe_flag = next((f for f in flags if f.get("code") in SAFE_AUTOPILOT_CODES), None)
            amount = float(claim.get("amount") or 0)
            at_risk = float(claim.get("at_risk") or 0)
            ts = _now_iso()
            patient = claim.get("patient", "")

            # Decide: escalate high-dollar or anything without a safe fixable flag.
            if safe_flag is None or amount > AUTOPILOT_HIGH_DOLLAR:
                reason = ("high-dollar claim — escalated for biller sign-off"
                          if safe_flag is not None
                          else "flag requires clinical/coding judgment — not auto-fixable")
                rationale = f"Queued for biller review: {reason}."
                conn.execute(
                    """INSERT INTO autopilot_log(ts,claim,patient,action,recovered,rationale)
                       VALUES(?,?,?,?,?,?)""",
                    (ts, claim["id"], patient, "queued", 0.0, rationale))
                conn.commit()
                st = _autopilot_state(conn)
                return {
                    "done": False, "action": "queued", "claim": claim["id"],
                    "patient": patient, "cpt": claim.get("cpt"), "payer": claim.get("payer"),
                    "amount": round(amount, 2), "recovered": 0.0,
                    "flag": (safe_flag or (flags[0] if flags else {})).get("name", "review"),
                    "correction": None, "rationale": rationale, "state": st,
                }, 200

            # Safe, low-dollar -> draft + apply the fix.
            correction, rationale = claude_autopilot_draft(claim, safe_flag)
            conn.execute("UPDATE claims SET status='Fixed' WHERE id=?", (claim["id"],))
            conn.execute(
                """INSERT INTO autopilot_log(ts,claim,patient,action,recovered,rationale)
                   VALUES(?,?,?,?,?,?)""",
                (ts, claim["id"], patient, "corrected", round(at_risk, 2),
                 correction + " — " + rationale))
            conn.commit()
            st = _autopilot_state(conn)
            return {
                "done": False, "action": "corrected", "claim": claim["id"],
                "patient": patient, "cpt": claim.get("cpt"), "payer": claim.get("payer"),
                "amount": round(amount, 2), "recovered": round(at_risk, 2),
                "flag": safe_flag.get("name"),
                "correction": correction, "rationale": rationale, "state": st,
            }, 200
        finally:
            conn.close()


def api_autopilot_log():
    conn = db()
    try:
        rows = [dict(r) for r in conn.execute(
            """SELECT ts,claim,patient,action,recovered,rationale
               FROM autopilot_log ORDER BY id DESC LIMIT 100""").fetchall()]
        return {"log": rows}, 200
    finally:
        conn.close()


def api_autopilot_state():
    conn = db()
    try:
        remaining = conn.execute(
            """SELECT COUNT(*) FROM claims
               WHERE status='At-Risk'
                 AND id NOT IN (SELECT claim FROM autopilot_log)""").fetchone()[0]
        st = _autopilot_state(conn)
        st["running"] = remaining > 0
        st["remaining"] = remaining
        return st, 200
    finally:
        conn.close()


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, {"error": "index.html missing"})
            return
        if path == "/api/dashboard":
            body, code = api_dashboard()
            return self._send(code, body)
        if path == "/api/ambient":
            body, code = api_ambient()
            return self._send(code, body)
        if path == "/api/analytics":
            body, code = api_analytics()
            return self._send(code, body)
        if path == "/api/autopilot/log":
            body, code = api_autopilot_log()
            return self._send(code, body)
        if path == "/api/autopilot/state":
            body, code = api_autopilot_state()
            return self._send(code, body)
        if path == "/api/health":
            return self._send(200, {"ok": True, "ai": bool(API_KEY)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        if path == "/api/scan":
            body, code = api_scan(payload)
            return self._send(code, body)
        if path == "/api/fix":
            body, code = api_fix(payload)
            return self._send(code, body)
        if path == "/api/batch_scan":
            body, code = api_batch_scan(payload)
            return self._send(code, body)
        if path == "/api/autopilot/step":
            body, code = api_autopilot_step(payload)
            return self._send(code, body)
        self._send(404, {"error": "not found"})


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    mode = "REAL Claude (" + CLAUDE_MODEL + ")" if API_KEY else "MOCK explanations"
    print(f"ClaimGuard on http://localhost:{PORT}  [{mode}]")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
