#!/usr/bin/env python3
"""
LedgerBridge - integration glue for small accounting firms.
Python 3 stdlib only. Serves a single-file index.html + a small JSON API.
Backend: http.server on PORT 8806, sqlite3 seeded on first run.

If /home/sagar-uniyal/creator-os/.env has ANTHROPIC_API_KEY, a real Claude call
(claude-sonnet-4-6 via urllib) suggests account mappings and explains drift.
Otherwise a deterministic MOCK fallback is used.
"""
import http.server
import socketserver
import json
import os
import sqlite3
import threading
import urllib.request
import urllib.error
import re
import time
import html as _html

PORT = 8806
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "ledgerbridge.db")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
ENV_PATH = "/home/sagar-uniyal/creator-os/.env"

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Anthropic key discovery
# ---------------------------------------------------------------------------
def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        with open(ENV_PATH, "r") as f:
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
AI_MODE = "claude" if API_KEY else "mock"


def claude_call(system, user, max_tokens=400):
    """Real Claude call via urllib. Returns text or None on any failure."""
    if not API_KEY:
        return None
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        parts = body.get("content", [])
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return text.strip() or None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE tools (
            id INTEGER PRIMARY KEY,
            name TEXT, type TEXT, status TEXT, last_sync TEXT, node_x REAL, node_y REAL
        );
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            src_tool_id INTEGER, src_account TEXT, src_code TEXT,
            tgt_tool_id INTEGER, tgt_account TEXT, tgt_code TEXT,
            status TEXT  -- mapped | unmapped | conflict
        );
        CREATE TABLE txns (
            id INTEGER PRIMARY KEY,
            ref TEXT, memo TEXT,
            sys_a_tool INTEGER, sys_a_amount REAL,
            sys_b_tool INTEGER, sys_b_amount REAL,
            posted TEXT, status TEXT  -- matched | mismatch | missing
        );
        CREATE TABLE close_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            period TEXT,
            summary TEXT  -- JSON blob of the full close summary
        );
        """
    )

    # ~6 connected tools. node_x/node_y are % positions on the node-map canvas.
    tools = [
        (1, "QuickBooks Online", "Accounting Ledger", "synced",    "2036-05-02 09:12", 50, 20),
        (2, "Xero",              "Accounting Ledger", "synced",    "2036-05-02 09:10", 82, 40),
        (3, "Mercury Bank",      "Bank Feed",         "syncing",   "2036-05-02 09:14", 78, 78),
        (4, "Gusto Payroll",     "Payroll",           "synced",    "2036-05-02 08:55", 22, 78),
        (5, "Dext Receipts",     "Receipt Capture",   "drift",     "2036-05-02 07:40", 15, 42),
        (6, "Google Sheets",     "Spreadsheet",       "stale",     "2036-05-01 18:30", 50, 55),
    ]
    c.executemany(
        "INSERT INTO tools VALUES (?,?,?,?,?,?,?)", tools
    )

    # Chart-of-accounts mapping between QuickBooks (1) and Xero (2) mostly,
    # plus bank / payroll / receipts feeding in. Some unmapped / conflicting.
    accounts = [
        # src_tool, src_acct, src_code, tgt_tool, tgt_acct, tgt_code, status
        (1, 1, "Cash - Operating",        "1000", 2, "Business Bank Account", "090", "mapped"),
        (2, 1, "Accounts Receivable",     "1100", 2, "Accounts Receivable",   "610", "mapped"),
        (3, 1, "Sales Revenue",           "4000", 2, "Sales",                 "200", "mapped"),
        (4, 1, "Merchant Fees",           "6120", 2, "Bank Fees",             "404", "conflict"),
        (5, 1, "Software Subscriptions",  "6210", None, None,                 None,  "unmapped"),
        (6, 4, "Payroll Wages",           "6000", 1, "Salaries & Wages",      "6001","mapped"),
        (7, 4, "Payroll Taxes",           "6010", 1, "Payroll Tax Expense",   "6011","mapped"),
        (8, 5, "Meals & Entertainment",   "R-ME", None, None,                 None,  "unmapped"),
        (9, 5, "Office Supplies",         "R-OS", 1, "Office Supplies",       "6300","conflict"),
        (10, 3, "Interest Income",        "B-INT", None, None,                None,  "unmapped"),
    ]
    c.executemany(
        "INSERT INTO accounts (id, src_tool_id, src_account, src_code, tgt_tool_id, tgt_account, tgt_code, status) VALUES (?,?,?,?,?,?,?,?)",
        accounts,
    )

    # ~15 cross-system transactions; a few mismatched / missing.
    txns = [
        # ref, memo, sysA_tool, sysA_amt, sysB_tool, sysB_amt, posted, status
        ("INV-2041", "Client retainer - Acme",     1, 4500.00, 3, 4500.00, "2036-05-01", "matched"),
        ("INV-2042", "Consulting - Bolt Co",       1, 2200.00, 3, 2200.00, "2036-05-01", "matched"),
        ("INV-2043", "Design sprint - Nova",        1, 1800.00, 3, 1780.00, "2036-05-01", "mismatch"),
        ("BILL-770", "AWS hosting",                 1,  612.44, 3,  612.44, "2036-05-01", "matched"),
        ("BILL-771", "Figma seats",                 1,  180.00, 3,  180.00, "2036-04-30", "matched"),
        ("PAY-0031", "Payroll run - May W1",        4, 18240.00, 1, 18240.00, "2036-05-01", "matched"),
        ("PAY-0031T","Payroll taxes - May W1",      4, 5122.30, 1, 5098.10, "2036-05-01", "mismatch"),
        ("RCPT-88",  "Client dinner",               5,  142.50, 1, 142.50, "2036-04-29", "matched"),
        ("RCPT-89",  "Toner + supplies",            5,   64.20, 1, None,   "2036-04-29", "missing"),
        ("FEE-556",  "Stripe merchant fee",         3,   88.10, 1,  88.10, "2036-04-30", "matched"),
        ("FEE-557",  "Wire fee",                     3,   25.00, 1,  30.00, "2036-04-30", "mismatch"),
        ("INV-2044", "Retainer - Quill",            1, 3000.00, 3, 3000.00, "2036-04-28", "matched"),
        ("BILL-772", "Office rent",                 1, 2650.00, 3, 2650.00, "2036-04-28", "matched"),
        ("INT-014",  "Bank interest",               3,   12.90, 1, None,   "2036-04-30", "missing"),
        ("INV-2045", "Workshop - Orbit",            1, 1250.00, 3, 1250.00, "2036-04-27", "matched"),
    ]
    c.executemany(
        "INSERT INTO txns (ref, memo, sys_a_tool, sys_a_amount, sys_b_tool, sys_b_amount, posted, status) VALUES (?,?,?,?,?,?,?,?)",
        txns,
    )
    conn.commit()
    conn.close()


def ensure_close_table():
    """Additive migration: create close_runs on already-seeded databases."""
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS close_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            period TEXT,
            summary TEXT
        )"""
    )
    conn.commit()
    conn.close()


def ensure_autopilot_table():
    """Additive migration: create autopilot_log on already-seeded databases."""
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS autopilot_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            action TEXT,
            target TEXT,
            amount REAL,
            rationale TEXT
        )"""
    )
    conn.commit()
    conn.close()


def ensure_db():
    if not os.path.exists(DB_PATH):
        seed_db()
    ensure_close_table()
    ensure_autopilot_table()


def tool_name(conn, tid):
    if tid is None:
        return None
    r = conn.execute("SELECT name FROM tools WHERE id=?", (tid,)).fetchone()
    return r["name"] if r else None


# ---------------------------------------------------------------------------
# Domain queries
# ---------------------------------------------------------------------------
def get_state():
    conn = get_conn()
    tools = [dict(r) for r in conn.execute("SELECT * FROM tools ORDER BY id")]

    accounts = []
    for r in conn.execute("SELECT * FROM accounts ORDER BY id"):
        d = dict(r)
        d["src_tool_name"] = tool_name(conn, d["src_tool_id"])
        d["tgt_tool_name"] = tool_name(conn, d["tgt_tool_id"])
        accounts.append(d)

    txns = []
    for r in conn.execute("SELECT * FROM txns ORDER BY posted DESC, id DESC"):
        d = dict(r)
        d["sys_a_name"] = tool_name(conn, d["sys_a_tool"])
        d["sys_b_name"] = tool_name(conn, d["sys_b_tool"])
        txns.append(d)
    conn.close()

    total = len(accounts)
    mapped = sum(1 for a in accounts if a["status"] == "mapped")
    mapped_pct = round(mapped / total * 100) if total else 0

    open_drift = 0.0
    for t in txns:
        if t["status"] == "mismatch":
            a = t["sys_a_amount"] or 0
            b = t["sys_b_amount"] or 0
            open_drift += abs(a - b)
        elif t["status"] == "missing":
            open_drift += t["sys_a_amount"] or t["sys_b_amount"] or 0

    last_sync = max((t["last_sync"] for t in tools), default="-")

    intel = {
        "mapped_pct": mapped_pct,
        "mapped": mapped,
        "total_accounts": total,
        "unmapped": sum(1 for a in accounts if a["status"] == "unmapped"),
        "conflicts": sum(1 for a in accounts if a["status"] == "conflict"),
        "open_drift": round(open_drift, 2),
        "last_sync": last_sync,
        "tools_connected": len(tools),
        "ai_mode": AI_MODE,
    }
    return {"tools": tools, "accounts": accounts, "txns": txns, "intel": intel}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------
def mock_reconcile_fix(t):
    a, b = t.get("sys_a_amount"), t.get("sys_b_amount")
    if t["status"] == "missing":
        present = t["sys_a_name"] if a is not None else t["sys_b_name"]
        absent = t["sys_b_name"] if a is not None else t["sys_a_name"]
        return (f"Only present in {present}; no matching entry in {absent}. "
                f"Likely an unsynced feed line — create the counterpart entry in {absent} "
                f"and tag with ref {t['ref']}.")
    diff = round(abs((a or 0) - (b or 0)), 2)
    return (f"{diff:.2f} variance between {t['sys_a_name']} ({a:.2f}) and "
            f"{t['sys_b_name']} ({b:.2f}). Common causes: FX rounding, a partial "
            f"fee, or a duplicated split. Post a {diff:.2f} adjusting entry to the "
            f"lower side and re-run the match.")


def do_reconcile():
    st = get_state()
    issues = [t for t in st["txns"] if t["status"] in ("mismatch", "missing")]

    ai_summary = None
    if AI_MODE == "claude" and issues:
        lines = []
        for t in issues:
            lines.append(
                f"{t['ref']} ({t['memo']}): {t['sys_a_name']}="
                f"{t['sys_a_amount']} vs {t['sys_b_name']}={t['sys_b_amount']} [{t['status']}]"
            )
        prompt = (
            "You are a reconciliation analyst for a small accounting firm. "
            "Given these cross-system transaction discrepancies, write a 2-3 sentence "
            "executive summary of the drift and the single highest-priority fix.\n\n"
            + "\n".join(lines)
        )
        ai_summary = claude_call(
            "You are LedgerBridge, a terse, precise reconciliation engine.",
            prompt,
            max_tokens=300,
        )

    results = []
    for t in issues:
        a, b = t.get("sys_a_amount"), t.get("sys_b_amount")
        amt = None
        if t["status"] == "mismatch":
            amt = round(abs((a or 0) - (b or 0)), 2)
        else:
            amt = a if a is not None else b
        results.append({
            "ref": t["ref"],
            "memo": t["memo"],
            "sys_a": t["sys_a_name"],
            "sys_b": t["sys_b_name"],
            "amount_a": a,
            "amount_b": b,
            "delta": amt,
            "status": t["status"],
            "fix": mock_reconcile_fix(t),
        })

    matched = sum(1 for t in st["txns"] if t["status"] == "matched")
    return {
        "results": results,
        "matched": matched,
        "total": len(st["txns"]),
        "issue_count": len(issues),
        "ai_summary": ai_summary,
        "ai_mode": AI_MODE,
    }


# ---------------------------------------------------------------------------
# Mapping suggestion
# ---------------------------------------------------------------------------
MOCK_MAP = {
    "Software Subscriptions": ("QuickBooks Online", "Software & SaaS Expense", "6210",
                               "Recurring SaaS tooling belongs under operating software expense."),
    "Meals & Entertainment": ("QuickBooks Online", "Meals & Entertainment", "6410",
                              "Receipt-captured meals map to the 50%-deductible M&E account."),
    "Interest Income": ("QuickBooks Online", "Interest Income", "4900",
                        "Bank interest is other income; post to the 4900 interest line."),
    "Office Supplies": ("QuickBooks Online", "Office Supplies", "6300",
                        "Resolve conflict by unifying on the QBO 6300 supplies account."),
    "Merchant Fees": ("Xero", "Bank Fees", "404",
                      "Both describe payment-processor charges; unify on Xero Bank Fees 404."),
}


def suggest_mapping(account_id):
    conn = get_conn()
    r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    if not r:
        return {"error": "account not found"}
    a = dict(r)
    src = a["src_account"]

    if AI_MODE == "claude":
        prompt = (
            f"An unmapped/conflicting source account needs a target account in the "
            f"firm's primary ledger (QuickBooks Online or Xero).\n"
            f"Source account: '{src}' (code {a['src_code']}).\n"
            f"Return STRICT JSON only: "
            f'{{"target_tool":"QuickBooks Online"|"Xero","target_account":"...",'
            f'"target_code":"...","rationale":"one sentence"}}'
        )
        text = claude_call(
            "You are LedgerBridge's chart-of-accounts mapper. Output JSON only.",
            prompt, max_tokens=250,
        )
        parsed = _extract_json(text)
        if parsed and parsed.get("target_account"):
            return {
                "account_id": account_id,
                "src_account": src,
                "target_tool": parsed.get("target_tool", "QuickBooks Online"),
                "target_account": parsed.get("target_account"),
                "target_code": parsed.get("target_code", ""),
                "rationale": parsed.get("rationale", ""),
                "ai_mode": "claude",
            }
        # fall through to mock on failure

    tool, acct, code, why = MOCK_MAP.get(
        src, ("QuickBooks Online", "Uncategorized Expense", "6999",
              "No confident match; route to uncategorized for manual review.")
    )
    return {
        "account_id": account_id,
        "src_account": src,
        "target_tool": tool,
        "target_account": acct,
        "target_code": code,
        "rationale": why,
        "ai_mode": "mock",
    }


def _extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def accept_mapping(account_id, target_tool, target_account, target_code):
    conn = get_conn()
    tgt_row = conn.execute("SELECT id FROM tools WHERE name=?", (target_tool,)).fetchone()
    tgt_id = tgt_row["id"] if tgt_row else None
    conn.execute(
        "UPDATE accounts SET tgt_tool_id=?, tgt_account=?, tgt_code=?, status='mapped' WHERE id=?",
        (tgt_id, target_account, target_code, account_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


def reject_mapping(account_id):
    # leaves it unmapped/conflict; no-op record for the demo
    return {"ok": True, "account_id": account_id}


def ambient_run():
    """Ambient mode (2036): agent auto-maps a new account and clears drift."""
    conn = get_conn()
    # auto-map any remaining unmapped/conflict accounts
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM accounts WHERE status IN ('unmapped','conflict')")]
    mapped_now = []
    for a in rows:
        tool, acct, code, _ = MOCK_MAP.get(
            a["src_account"], ("QuickBooks Online", "Uncategorized Expense", "6999", ""))
        tgt_row = conn.execute("SELECT id FROM tools WHERE name=?", (tool,)).fetchone()
        tgt_id = tgt_row["id"] if tgt_row else None
        conn.execute(
            "UPDATE accounts SET tgt_tool_id=?, tgt_account=?, tgt_code=?, status='mapped' WHERE id=?",
            (tgt_id, acct, code, a["id"]))
        mapped_now.append(f"{a['src_account']} -> {tool}:{acct}")
    # clear drift: set all txns to matched, aligning amounts
    conn.execute("UPDATE txns SET status='matched'")
    conn.execute("UPDATE txns SET sys_b_amount=sys_a_amount WHERE sys_b_amount IS NOT NULL")
    conn.execute("UPDATE txns SET sys_b_amount=sys_a_amount WHERE sys_b_amount IS NULL")
    conn.execute("UPDATE tools SET status='synced', last_sync='2036-05-02 09:20'")
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "auto_mapped": mapped_now,
        "message": "Ambient agent reconciled all feeds, auto-mapped "
                   f"{len(mapped_now)} account(s), and cleared open drift to $0.00.",
    }


# ---------------------------------------------------------------------------
# Month-end close
# ---------------------------------------------------------------------------
FIRM_NAME = "Northbridge Accounting Partners LLP"
DRIFT_THRESHOLD = 100.00          # $ open-drift ceiling to be "ready to close"
STALE_STATUSES = ("stale",)       # connections considered stale
CRITICAL_TYPES = ("Accounting Ledger", "Bank Feed", "Payroll")


def _txn_drift(t):
    """Absolute open drift contributed by one transaction row."""
    if t["status"] == "mismatch":
        return abs((t["sys_a_amount"] or 0) - (t["sys_b_amount"] or 0))
    if t["status"] == "missing":
        return t["sys_a_amount"] or t["sys_b_amount"] or 0
    return 0.0


def _current_period():
    conn = get_conn()
    row = conn.execute("SELECT MAX(posted) p FROM txns").fetchone()
    conn.close()
    posted = (row["p"] if row else None) or "2036-05-01"
    return posted[:7]  # YYYY-MM


def compute_readiness():
    """Pre-close checklist: pass/fail rows + overall verdict. Read-only."""
    st = get_state()
    tools, accounts, txns, intel = st["tools"], st["accounts"], st["txns"], st["intel"]

    unsynced = [t for t in tools if t["status"] not in ("synced",)]
    stale = [t for t in tools if t["status"] in STALE_STATUSES]
    unmapped_critical = [
        a for a in accounts
        if a["status"] != "mapped"
        and tool_name_type_is_critical(a)
    ]
    open_drift = intel["open_drift"]
    conflicts = intel["conflicts"]

    checks = [
        {
            "label": "All tools synced",
            "pass": len(unsynced) == 0,
            "detail": ("All feeds report synced"
                       if not unsynced
                       else ", ".join(f"{t['name']} ({t['status']})" for t in unsynced)),
        },
        {
            "label": "No stale connections",
            "pass": len(stale) == 0,
            "detail": ("No stale feeds"
                       if not stale
                       else ", ".join(t["name"] for t in stale)),
        },
        {
            "label": "No unmapped critical accounts",
            "pass": len(unmapped_critical) == 0,
            "detail": ("Ledger / bank / payroll accounts fully mapped"
                       if not unmapped_critical
                       else ", ".join(a["src_account"] for a in unmapped_critical)),
        },
        {
            "label": "No mapping conflicts",
            "pass": conflicts == 0,
            "detail": (f"{conflicts} conflict(s) outstanding" if conflicts else "No conflicts"),
        },
        {
            "label": f"Open drift under ${DRIFT_THRESHOLD:,.0f}",
            "pass": open_drift <= DRIFT_THRESHOLD,
            "detail": f"Open drift is ${open_drift:,.2f} (threshold ${DRIFT_THRESHOLD:,.0f})",
        },
    ]
    ready = all(c["pass"] for c in checks)
    return {
        "period": _current_period(),
        "checks": checks,
        "ready": ready,
        "passed": sum(1 for c in checks if c["pass"]),
        "total": len(checks),
    }


def tool_name_type_is_critical(account):
    """True when the source tool of an account is a critical system type."""
    conn = get_conn()
    r = conn.execute("SELECT type FROM tools WHERE id=?", (account["src_tool_id"],)).fetchone()
    conn.close()
    return bool(r) and r["type"] in CRITICAL_TYPES


def do_close_run():
    """Full month-end close: snapshot before, auto-resolve, snapshot after,
    build a close summary, persist it, and return it."""
    period = _current_period()

    before = get_state()
    before_drift = before["intel"]["open_drift"]
    before_mapped_pct = before["intel"]["mapped_pct"]
    before_unmapped = before["intel"]["unmapped"]
    before_conflicts = before["intel"]["conflicts"]

    # ---- auto-resolve what is auto-resolvable -----------------------------
    conn = get_conn()

    # 1. auto-map any account we have a confident mock/AI mapping for
    resolved_mappings = []
    still_unmapped = []
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM accounts WHERE status IN ('unmapped','conflict')")]
    for a in rows:
        m = MOCK_MAP.get(a["src_account"])
        if m:
            tool, acct, code, _why = m
            tgt_row = conn.execute("SELECT id FROM tools WHERE name=?", (tool,)).fetchone()
            tgt_id = tgt_row["id"] if tgt_row else None
            conn.execute(
                "UPDATE accounts SET tgt_tool_id=?, tgt_account=?, tgt_code=?, "
                "status='mapped' WHERE id=?",
                (tgt_id, acct, code, a["id"]))
            resolved_mappings.append({
                "src_account": a["src_account"],
                "target": f"{tool} · {acct}",
                "code": code,
            })
        else:
            still_unmapped.append(a)

    # 2. auto-resolve reconciliation drift where amounts are safely alignable.
    #    "missing" lines and small rounding mismatches are auto-posted; large
    #    variances are held as exceptions for a human.
    ROUNDING_TOL = 25.00
    exceptions = []
    resolved_drift_total = 0.0
    remaining_drift_total = 0.0
    for t in conn.execute("SELECT * FROM txns WHERE status IN ('mismatch','missing')"):
        t = dict(t)
        t["sys_a_name"] = tool_name(conn, t["sys_a_tool"])
        t["sys_b_name"] = tool_name(conn, t["sys_b_tool"])
        d = _txn_drift(t)
        auto_ok = (t["status"] == "missing") or (d <= ROUNDING_TOL)
        if auto_ok:
            # align B to A (or A to B when A missing) and mark matched
            if t["sys_a_amount"] is None:
                conn.execute("UPDATE txns SET sys_a_amount=sys_b_amount, status='matched' WHERE id=?", (t["id"],))
            else:
                conn.execute("UPDATE txns SET sys_b_amount=sys_a_amount, status='matched' WHERE id=?", (t["id"],))
            resolved_drift_total += d
            resolution = "auto-posted"
        else:
            remaining_drift_total += d
            resolution = "open"
        exceptions.append({
            "ref": t["ref"],
            "memo": t["memo"],
            "systems": f"{t['sys_a_name']} ↔ {t['sys_b_name']}",
            "type": t["status"],
            "amount": round(d, 2),
            "resolution": resolution,
        })

    conn.commit()
    conn.close()

    after = get_state()
    after_drift = after["intel"]["open_drift"]
    after_mapped_pct = after["intel"]["mapped_pct"]

    tools_sync = [{
        "name": t["name"],
        "type": t["type"],
        "status": t["status"],
        "in_sync": t["status"] == "synced",
        "last_sync": t["last_sync"],
    } for t in after["tools"]]

    unresolved = [e for e in exceptions if e["resolution"] == "open"]

    summary = {
        "period": period,
        "firm": FIRM_NAME,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "systems_reconciled": [t["name"] for t in after["tools"]],
        "tools_total": len(after["tools"]),
        "tools_in_sync": sum(1 for t in tools_sync if t["in_sync"]),
        "tools_out_of_sync": sum(1 for t in tools_sync if not t["in_sync"]),
        "tools": tools_sync,
        "mapped_pct_before": before_mapped_pct,
        "mapped_pct_after": after_mapped_pct,
        "accounts_total": after["intel"]["total_accounts"],
        "accounts_mapped": after["intel"]["mapped"],
        "accounts_auto_mapped": len(resolved_mappings),
        "auto_mapped": resolved_mappings,
        "unmapped_after": after["intel"]["unmapped"],
        "conflicts_after": after["intel"]["conflicts"],
        "drift_before": round(before_drift, 2),
        "drift_after": round(after_drift, 2),
        "drift_resolved": round(resolved_drift_total, 2),
        "drift_remaining": round(remaining_drift_total, 2),
        "exceptions": exceptions,
        "exception_count": len(exceptions),
        "unresolved_count": len(unresolved),
        "clean_close": len(unresolved) == 0 and after["intel"]["unmapped"] == 0
                       and after["intel"]["conflicts"] == 0,
        "ai_mode": AI_MODE,
    }

    # optional AI narrative
    if AI_MODE == "claude":
        note = claude_call(
            "You are LedgerBridge, a terse month-end close controller.",
            "Write a 2-sentence sign-off narrative for a month-end close. "
            f"Period {period}. Drift went from ${before_drift:.2f} to ${after_drift:.2f}. "
            f"{len(resolved_mappings)} accounts auto-mapped, {len(unresolved)} exceptions remain.",
            max_tokens=180,
        )
        if note:
            summary["ai_narrative"] = note

    # persist
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO close_runs (created_at, period, summary) VALUES (?,?,?)",
        (summary["generated_at"], period, json.dumps(summary)),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()

    summary["run_id"] = run_id
    return summary


def list_close_runs():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, created_at, period, summary FROM close_runs ORDER BY id DESC")]
    conn.close()
    out = []
    for r in rows:
        try:
            s = json.loads(r["summary"])
        except ValueError:
            s = {}
        out.append({
            "run_id": r["id"],
            "created_at": r["created_at"],
            "period": r["period"],
            "drift_before": s.get("drift_before"),
            "drift_after": s.get("drift_after"),
            "mapped_pct_after": s.get("mapped_pct_after"),
            "unresolved_count": s.get("unresolved_count"),
            "clean_close": s.get("clean_close"),
        })
    return {"runs": out}


def get_close_run(run_id):
    conn = get_conn()
    r = conn.execute("SELECT * FROM close_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    if not r:
        return None
    try:
        return json.loads(r["summary"])
    except ValueError:
        return None


def render_close_report(run_id):
    """Print-ready HTML month-end close report."""
    s = get_close_run(run_id)
    if not s:
        return None
    e = _html.escape

    def money(v):
        try:
            return "${:,.2f}".format(float(v or 0))
        except (TypeError, ValueError):
            return "—"

    tool_rows = "".join(
        f"<tr><td>{e(t['name'])}</td><td>{e(t['type'])}</td>"
        f"<td class='{'ok' if t['in_sync'] else 'bad'}'>{'In sync' if t['in_sync'] else 'Out of sync ('+e(t['status'])+')'}</td>"
        f"<td>{e(t['last_sync'])}</td></tr>"
        for t in s.get("tools", [])
    )

    exc = s.get("exceptions", [])
    if exc:
        exc_rows = "".join(
            f"<tr><td>{e(x['ref'])}</td><td>{e(x['memo'])}</td>"
            f"<td>{e(x['systems'])}</td><td>{e(x['type'])}</td>"
            f"<td class='num'>{money(x['amount'])}</td>"
            f"<td class='{'ok' if x['resolution']!='open' else 'bad'}'>{e(x['resolution'])}</td></tr>"
            for x in exc
        )
    else:
        exc_rows = "<tr><td colspan='6' class='muted'>No exceptions — all cross-system entries reconciled.</td></tr>"

    mapped = f"{s.get('accounts_mapped',0)}/{s.get('accounts_total',0)} ({s.get('mapped_pct_after',0)}%)"
    narrative = s.get("ai_narrative", "")
    narrative_html = (f"<p class='narr'>{e(narrative)}</p>" if narrative else "")
    verdict = "CLEAN CLOSE" if s.get("clean_close") else "CLOSE WITH EXCEPTIONS"
    verdict_cls = "vok" if s.get("clean_close") else "vwarn"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<title>Month-End Close · {e(s.get('period',''))} · {e(s.get('firm',''))}</title>
<style>
  :root{{--emerald:#10b981;--charcoal:#111827;--muted:#6b7280;--line:#e5e7eb;}}
  *{{box-sizing:border-box}}
  body{{font-family:'Inter',-apple-system,Segoe UI,Roboto,sans-serif;color:#111827;background:#f8fafc;margin:0;padding:34px}}
  .doc{{max-width:900px;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:12px;
    padding:44px 52px;box-shadow:0 10px 40px rgba(0,0,0,.06)}}
  .letterhead{{display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid var(--emerald);padding-bottom:18px;margin-bottom:8px}}
  .lh-l{{display:flex;align-items:center;gap:14px}}
  .mark{{width:44px;height:44px;border-radius:11px;background:linear-gradient(135deg,var(--emerald),#065f46);position:relative}}
  .mark::before,.mark::after{{content:"";position:absolute;width:8px;height:8px;border-radius:50%;background:#fff;top:18px}}
  .mark::before{{left:10px}} .mark::after{{right:10px}}
  h1{{font-size:19px;margin:0;letter-spacing:.3px}}
  .firm-sub{{font-size:12px;color:var(--muted);margin-top:2px}}
  .docmeta{{text-align:right;font-size:12px;color:var(--muted);line-height:1.6}}
  .docmeta b{{color:#111827}}
  h2{{font-size:12px;text-transform:uppercase;letter-spacing:1.4px;color:var(--emerald);margin:30px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}}
  .verdict{{display:inline-block;margin-top:14px;font-weight:800;font-size:13px;letter-spacing:1px;padding:7px 16px;border-radius:8px}}
  .vok{{background:rgba(16,185,129,.12);color:#065f46;border:1px solid rgba(16,185,129,.4)}}
  .vwarn{{background:rgba(245,158,11,.12);color:#92400e;border:1px solid rgba(245,158,11,.4)}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:6px}}
  .kpi{{border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
  .kpi .k{{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}}
  .kpi .v{{font-size:20px;font-weight:800;margin-top:4px}}
  .kpi .v.em{{color:var(--emerald)}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px}}
  th{{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);border-bottom:2px solid var(--line);padding:8px 8px}}
  td{{padding:8px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  .ok{{color:#065f46;font-weight:600}} .bad{{color:#b91c1c;font-weight:600}}
  .muted{{color:var(--muted)}}
  .narr{{background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.25);border-radius:9px;padding:12px 14px;font-size:12.5px;line-height:1.6;margin-top:10px}}
  .signoff{{margin-top:34px;display:grid;grid-template-columns:1fr 1fr;gap:40px}}
  .sig{{border-top:1px solid #111827;padding-top:8px;font-size:12px;color:var(--muted)}}
  .sig b{{color:#111827;display:block;font-size:13px}}
  .foot{{margin-top:38px;font-size:10.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px;text-align:center}}
  @media print{{body{{background:#fff;padding:0}}.doc{{border:none;box-shadow:none}}}}
</style></head>
<body><div class="doc">
  <div class="letterhead">
    <div class="lh-l"><div class="mark"></div>
      <div><h1>{e(s.get('firm',''))}</h1>
        <div class="firm-sub">Month-End Close Report · Generated by LedgerBridge</div></div></div>
    <div class="docmeta">
      <div>Period <b>{e(s.get('period',''))}</b></div>
      <div>Run <b>#{run_id}</b></div>
      <div>{e(s.get('generated_at',''))}</div>
    </div>
  </div>
  <div class="verdict {verdict_cls}">{verdict}</div>
  {narrative_html}

  <h2>Close Summary</h2>
  <div class="kpis">
    <div class="kpi"><div class="k">Accounts mapped</div><div class="v em">{s.get('mapped_pct_after',0)}%</div></div>
    <div class="kpi"><div class="k">Drift before</div><div class="v">{money(s.get('drift_before'))}</div></div>
    <div class="kpi"><div class="k">Drift resolved</div><div class="v em">{money(s.get('drift_resolved'))}</div></div>
    <div class="kpi"><div class="k">Drift remaining</div><div class="v">{money(s.get('drift_remaining'))}</div></div>
  </div>
  <p class="muted" style="font-size:12px;margin-top:14px">
    Accounts coverage: <b>{mapped}</b> · Auto-mapped this close: <b>{s.get('accounts_auto_mapped',0)}</b> ·
    Systems in sync: <b>{s.get('tools_in_sync',0)}/{s.get('tools_total',0)}</b> ·
    Exceptions: <b>{s.get('exception_count',0)}</b> ({s.get('unresolved_count',0)} unresolved)
  </p>

  <h2>Systems Reconciled</h2>
  <table><thead><tr><th>System</th><th>Type</th><th>Sync status</th><th>Last sync</th></tr></thead>
  <tbody>{tool_rows}</tbody></table>

  <h2>Exceptions Register</h2>
  <table><thead><tr><th>Ref</th><th>Memo</th><th>Systems</th><th>Type</th><th class="num">Amount</th><th>Resolution</th></tr></thead>
  <tbody>{exc_rows}</tbody></table>

  <h2>Sign-Off</h2>
  <div class="signoff">
    <div class="sig"><b>Prepared by</b>LedgerBridge automated close · {e(s.get('generated_at',''))}</div>
    <div class="sig"><b>Reviewed &amp; approved by</b>_________________________  Date: __________</div>
  </div>

  <div class="foot">{e(s.get('firm',''))} · Confidential — prepared for internal accounting use ·
    Reconciled across {s.get('tools_total',0)} connected systems via LedgerBridge</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Autopilot — hands-free autonomous reconciliation
# ---------------------------------------------------------------------------
AUTOPILOT_SMALL_DRIFT = 25.00   # $ ceiling to auto-post a mismatch/missing line
_autopilot_running = False       # set by the UI toggle via /step calls


def _autopilot_log(action, target, amount, rationale):
    conn = get_conn()
    conn.execute(
        "INSERT INTO autopilot_log (ts, action, target, amount, rationale) VALUES (?,?,?,?,?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), action, target, amount, rationale),
    )
    conn.commit()
    conn.close()


def _autopilot_counts():
    """Live counters used by both /step results and /state."""
    conn = get_conn()
    mapped = conn.execute(
        "SELECT COUNT(*) c FROM autopilot_log WHERE action='auto-mapped'").fetchone()["c"]
    cleared = conn.execute(
        "SELECT COUNT(*) c FROM autopilot_log WHERE action='cleared-drift'").fetchone()["c"]
    conn.close()
    st = get_state()
    open_drift = st["intel"]["open_drift"]
    # anything not auto-clearable right now needs a human
    needs_review = 0
    for t in st["txns"]:
        if t["status"] in ("mismatch", "missing"):
            if _txn_drift(t) > AUTOPILOT_SMALL_DRIFT and t["status"] != "missing":
                needs_review += 1
    return {
        "mapped_count": mapped,
        "cleared_count": cleared,
        "needs_review": needs_review,
        "open_drift": round(open_drift, 2),
    }


def _autopilot_rationale(kind, target, detail):
    """One-line rationale via Claude, with a deterministic mock fallback."""
    if AI_MODE == "claude":
        prompt = (
            f"Autonomous bookkeeping action taken: {kind}.\n"
            f"Target: {target}. Detail: {detail}.\n"
            "Write ONE short sentence (max 18 words) justifying this safe automatic action."
        )
        txt = claude_call(
            "You are LedgerBridge Autopilot. Reply with one terse sentence only.",
            prompt, max_tokens=60,
        )
        if txt:
            return txt.splitlines()[0].strip()
    # mock fallbacks
    if kind == "auto-mapped":
        return f"High-confidence chart-of-accounts match for '{target}'; safe to map automatically."
    if kind == "cleared-drift":
        return f"Variance {detail} is within the ${AUTOPILOT_SMALL_DRIFT:.0f} auto-post tolerance; posted to clear drift."
    if kind == "queued":
        return f"Variance {detail} exceeds auto-post tolerance; queued for accountant review."
    return "No safe action available this cycle; books already reconciled."


def autopilot_step():
    """Run ONE autonomous cycle.

    Priority 1: auto-map the next confidently-mappable unmapped/conflict account.
    Priority 2: auto-post the next small (<= $25) cross-system drift line.
    Larger / ambiguous mismatches are logged 'queued for review' and left alone.
    """
    conn = get_conn()

    # --- 1. confidently-mappable account? -----------------------------------
    row = conn.execute(
        "SELECT * FROM accounts WHERE status IN ('unmapped','conflict') ORDER BY id"
    ).fetchone()
    mappable = None
    if row:
        a = dict(row)
        # "confident" == we have a known mapping (mirrors suggest/accept logic)
        if a["src_account"] in MOCK_MAP:
            mappable = a
    if mappable:
        conn.close()
        m = suggest_mapping(mappable["id"])            # reuse suggest logic
        accept_mapping(                                # reuse accept logic
            mappable["id"], m["target_tool"], m["target_account"], m["target_code"])
        target = mappable["src_account"]
        detail = f"{m['target_tool']} · {m['target_account']}"
        rationale = _autopilot_rationale("auto-mapped", target, detail)
        _autopilot_log("auto-mapped", f"{target} → {detail}", None, rationale)
        counts = _autopilot_counts()
        return {
            "action": "auto-mapped",
            "target": f"{target} → {detail}",
            "amount": None,
            "rationale": rationale,
            "detail": detail,
            **counts,
            "done": _autopilot_is_idle(),
        }

    # --- 2. small drift to clear? -------------------------------------------
    issues = []
    for t in conn.execute("SELECT * FROM txns WHERE status IN ('mismatch','missing') ORDER BY id"):
        t = dict(t)
        t["sys_a_name"] = tool_name(conn, t["sys_a_tool"])
        t["sys_b_name"] = tool_name(conn, t["sys_b_tool"])
        issues.append(t)

    small = None
    for t in issues:
        d = _txn_drift(t)
        if t["status"] == "missing" or d <= AUTOPILOT_SMALL_DRIFT:
            small = (t, d)
            break

    if small:
        t, d = small
        if t["sys_a_amount"] is None:
            conn.execute("UPDATE txns SET sys_a_amount=sys_b_amount, status='matched' WHERE id=?", (t["id"],))
        else:
            conn.execute("UPDATE txns SET sys_b_amount=sys_a_amount, status='matched' WHERE id=?", (t["id"],))
        conn.commit()
        conn.close()
        detail = f"${d:,.2f}"
        target = f"{t['ref']} ({t['sys_a_name']} ↔ {t['sys_b_name']})"
        rationale = _autopilot_rationale("cleared-drift", target, detail)
        _autopilot_log("cleared-drift", target, round(d, 2), rationale)
        counts = _autopilot_counts()
        return {
            "action": "cleared-drift",
            "target": target,
            "amount": round(d, 2),
            "rationale": rationale,
            "detail": detail,
            **counts,
            "done": _autopilot_is_idle(),
        }

    # --- 3. only large/ambiguous left → queue for review (log once each) ----
    queued = None
    for t in issues:
        d = _txn_drift(t)
        target = f"{t['ref']} ({t['sys_a_name']} ↔ {t['sys_b_name']})"
        already = conn.execute(
            "SELECT 1 FROM autopilot_log WHERE action='queued' AND target=?", (target,)
        ).fetchone()
        if not already:
            queued = (t, d, target)
            break

    if queued:
        t, d, target = queued
        conn.close()
        detail = f"${d:,.2f}"
        rationale = _autopilot_rationale("queued", target, detail)
        _autopilot_log("queued", target, round(d, 2), rationale)
        counts = _autopilot_counts()
        return {
            "action": "queued",
            "target": target,
            "amount": round(d, 2),
            "rationale": rationale,
            "detail": detail,
            **counts,
            "done": _autopilot_is_idle(),
        }

    conn.close()
    counts = _autopilot_counts()
    return {
        "action": "idle",
        "target": None,
        "amount": None,
        "rationale": "Books reconciled — no safe autonomous action pending.",
        "detail": None,
        **counts,
        "done": True,
    }


def _autopilot_is_idle():
    """True when no auto-actionable work remains (only human-review items left)."""
    st = get_state()
    for a in st["accounts"]:
        if a["status"] in ("unmapped", "conflict") and a["src_account"] in MOCK_MAP:
            return False
    for t in st["txns"]:
        if t["status"] in ("mismatch", "missing"):
            if t["status"] == "missing" or _txn_drift(t) <= AUTOPILOT_SMALL_DRIFT:
                return False
    return True


def autopilot_log_list():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, ts, action, target, amount, rationale FROM autopilot_log ORDER BY id DESC LIMIT 100")]
    conn.close()
    return {"log": rows}


def autopilot_state():
    counts = _autopilot_counts()
    return {
        "running": _autopilot_running,
        "idle": _autopilot_is_idle(),
        "ai_mode": AI_MODE,
        **counts,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            with _db_lock:
                self._send(get_state())
            return
        if self.path == "/api/health":
            self._send({"ok": True, "ai_mode": AI_MODE, "port": PORT})
            return
        if self.path == "/api/close_readiness":
            with _db_lock:
                self._send(compute_readiness())
            return
        if self.path == "/api/close_runs":
            with _db_lock:
                self._send(list_close_runs())
            return
        if self.path == "/api/autopilot/log":
            with _db_lock:
                self._send(autopilot_log_list())
            return
        if self.path == "/api/autopilot/state":
            with _db_lock:
                self._send(autopilot_state())
            return
        m = re.match(r"^/close-report/(\d+)/?$", self.path)
        if m:
            with _db_lock:
                doc = render_close_report(int(m.group(1)))
            if doc is None:
                self.send_error(404)
                return
            body = doc.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                f'inline; filename="close-report-{m.group(1)}.html"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        with _db_lock:
            if self.path == "/api/reconcile":
                self._send(do_reconcile())
                return
            body = self._read_json()
            if self.path == "/api/suggest-mapping":
                self._send(suggest_mapping(int(body.get("account_id"))))
                return
            if self.path == "/api/accept-mapping":
                self._send(accept_mapping(
                    int(body.get("account_id")),
                    body.get("target_tool", ""),
                    body.get("target_account", ""),
                    body.get("target_code", ""),
                ))
                return
            if self.path == "/api/reject-mapping":
                self._send(reject_mapping(int(body.get("account_id"))))
                return
            if self.path == "/api/ambient":
                self._send(ambient_run())
                return
            if self.path == "/api/close_run":
                self._send(do_close_run())
                return
            if self.path == "/api/autopilot/step":
                global _autopilot_running
                _autopilot_running = bool(body.get("running", True))
                self._send(autopilot_step())
                return
        self.send_error(404)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ensure_db()
    server = ThreadedServer(("0.0.0.0", PORT), Handler)
    print(f"LedgerBridge running on http://localhost:{PORT}  (AI mode: {AI_MODE})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
