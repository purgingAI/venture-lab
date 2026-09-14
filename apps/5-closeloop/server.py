#!/usr/bin/env python3
"""CloseLoop - real-estate transaction reconciliation console.

Python 3 stdlib only. http.server + sqlite3. Serves a single-file index.html.
Optional real Claude call (claude-sonnet-4-6) if creator-os/.env has a key,
MOCK fallback otherwise.
"""
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def money(n):
    return "$" + "{:,.2f}".format(float(n))

PORT = 8805
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "closeloop.db")
INDEX_PATH = os.path.join(HERE, "index.html")
ENV_PATH = "/home/sagar-uniyal/creator-os/.env"
CLAUDE_MODEL = "claude-sonnet-4-6"


# --------------------------------------------------------------------------
# API key + Claude
# --------------------------------------------------------------------------
def load_api_key():
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


def claude_explain(exception, txn):
    """Return a plain-English explanation + fix for an exception."""
    prompt = (
        "You are an escrow reconciliation analyst for a real-estate brokerage. "
        "A reconciliation exception was detected on a transaction. In 2-3 short "
        "sentences, explain in plain English what the discrepancy means and "
        "recommend the concrete fix a closing coordinator should take. Be specific "
        "and terse.\n\n"
        f"Property: {txn['property']}\n"
        f"Exception type: {exception['type']}\n"
        f"Line item: {exception['label']}\n"
        f"Expected: ${exception['expected']:,.2f}\n"
        f"Actual: ${exception['actual']:,.2f}\n"
        f"Variance: ${exception['variance']:,.2f}\n"
        f"Severity: {exception['severity']}\n"
    )
    if API_KEY:
        try:
            return claude_call(prompt)
        except Exception:
            pass  # fall through to mock
    return mock_explain(exception)


def claude_call(prompt):
    body = json.dumps(
        {
            "model": CLAUDE_MODEL,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
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
        data = json.loads(resp.read().decode())
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip() or mock_explain_generic()


def mock_explain_generic():
    return ("This line item does not reconcile between the expected settlement "
            "figure and the actual money movement. Have the closing coordinator "
            "confirm the correct amount with the title company and re-issue the "
            "settlement statement.")


def mock_explain(exception):
    t = exception["type"]
    var = exception["variance"]
    if t == "MISSING_EARNEST":
        return ("The earnest money deposit expected at contract has not landed in "
                "escrow. This blocks clear-to-close. Contact the buyer's agent to "
                "confirm the wire and chase proof of deposit before appraisal.")
    if t == "COMMISSION_MISMATCH":
        side = "over" if var > 0 else "under"
        return (f"The commission actually disbursed is {side}-paid by "
                f"${abs(var):,.2f} versus the agreed split. Recompute the split "
                "against the listing agreement and correct the disbursement line "
                "on the settlement statement.")
    if t == "FEE_DISCREPANCY":
        return (f"A title/escrow fee differs from the quoted figure by "
                f"${abs(var):,.2f}. Pull the title company's fee schedule, confirm "
                "which party is charged, and reconcile the CD line item before "
                "funding.")
    if t == "CREDIT_MISMATCH":
        return (f"Seller credits applied differ from the contract by "
                f"${abs(var):,.2f}. Verify the negotiated credit in the purchase "
                "agreement addenda and adjust the buyer's cash-to-close.")
    return mock_explain_generic()


# --------------------------------------------------------------------------
# Seed data
# --------------------------------------------------------------------------
# Each txn: line items with (key, label, expected, actual).
SEED = [
    {
        "id": "TX-4021",
        "property": "418 Marlow Ridge, Austin TX",
        "buyer": "J. Okafor",
        "seller": "R. Delgado",
        "close_date": "2036-04-18",
        "days_to_close": 31,
        "timeline": "earnest,inspection,appraisal,clear,funded",
        "items": [
            ("sale_price", "Sale Price", 612000, 612000),
            ("earnest", "Earnest Money", 18000, 18000),
            ("loan", "Loan Amount", 489600, 489600),
            ("seller_credit", "Seller Credits", 6000, 6000),
            ("commission", "Commission Split", 18360, 18360),
            ("title_fee", "Title / Escrow Fees", 2450, 2450),
        ],
    },
    {
        "id": "TX-4022",
        "property": "77 Sable Court, Round Rock TX",
        "buyer": "M. Petrova",
        "seller": "T. Nguyen",
        "close_date": "2036-04-22",
        "days_to_close": 44,
        "timeline": "earnest,inspection,appraisal",
        "items": [
            ("sale_price", "Sale Price", 398000, 398000),
            ("earnest", "Earnest Money", 12000, 0),  # MISSING earnest
            ("loan", "Loan Amount", 318400, 318400),
            ("seller_credit", "Seller Credits", 0, 0),
            ("commission", "Commission Split", 11940, 11940),
            ("title_fee", "Title / Escrow Fees", 1980, 1980),
        ],
    },
    {
        "id": "TX-4023",
        "property": "1902 Kestrel Bend, Cedar Park TX",
        "buyer": "A. Bianchi",
        "seller": "L. Owusu",
        "close_date": "2036-04-15",
        "days_to_close": 28,
        "timeline": "earnest,inspection,appraisal,clear,funded",
        "items": [
            ("sale_price", "Sale Price", 725000, 725000),
            ("earnest", "Earnest Money", 21750, 21750),
            ("loan", "Loan Amount", 580000, 580000),
            ("seller_credit", "Seller Credits", 8000, 8000),
            ("commission", "Commission Split", 21750, 23400),  # COMMISSION over
            ("title_fee", "Title / Escrow Fees", 3100, 3100),
        ],
    },
    {
        "id": "TX-4024",
        "property": "56 Juniper Flat, Leander TX",
        "buyer": "S. Haddad",
        "seller": "P. Larsson",
        "close_date": "2036-05-02",
        "days_to_close": 52,
        "timeline": "earnest,inspection",
        "items": [
            ("sale_price", "Sale Price", 289000, 289000),
            ("earnest", "Earnest Money", 8670, 8670),
            ("loan", "Loan Amount", 231200, 231200),
            ("seller_credit", "Seller Credits", 3500, 5000),  # CREDIT mismatch
            ("commission", "Commission Split", 8670, 8670),
            ("title_fee", "Title / Escrow Fees", 1620, 1875),  # FEE discrepancy
        ],
    },
    {
        "id": "TX-4025",
        "property": "330 Aspen Hollow, Georgetown TX",
        "buyer": "D. Ferreira",
        "seller": "K. Yamamoto",
        "close_date": "2036-04-11",
        "days_to_close": 24,
        "timeline": "earnest,inspection,appraisal,clear,funded",
        "items": [
            ("sale_price", "Sale Price", 465000, 465000),
            ("earnest", "Earnest Money", 13950, 13950),
            ("loan", "Loan Amount", 372000, 372000),
            ("seller_credit", "Seller Credits", 4200, 4200),
            ("commission", "Commission Split", 13950, 13950),
            ("title_fee", "Title / Escrow Fees", 2210, 2210),
        ],
    },
    {
        "id": "TX-4026",
        "property": "12 Wren Terrace, Pflugerville TX",
        "buyer": "C. Mbeki",
        "seller": "V. Rossi",
        "close_date": "2036-04-29",
        "days_to_close": 39,
        "timeline": "earnest,inspection,appraisal,clear",
        "items": [
            ("sale_price", "Sale Price", 351000, 351000),
            ("earnest", "Earnest Money", 10530, 10530),
            ("loan", "Loan Amount", 280800, 280800),
            ("seller_credit", "Seller Credits", 2000, 2000),
            ("commission", "Commission Split", 10530, 9800),  # COMMISSION under
            ("title_fee", "Title / Escrow Fees", 1750, 1750),
        ],
    },
]

# Which line-item keys map to which exception type.
EXC_TYPE = {
    "earnest": "MISSING_EARNEST",
    "commission": "COMMISSION_MISMATCH",
    "title_fee": "FEE_DISCREPANCY",
    "seller_credit": "CREDIT_MISMATCH",
    "loan": "FEE_DISCREPANCY",
    "sale_price": "FEE_DISCREPANCY",
}


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS txn (
            id TEXT PRIMARY KEY, property TEXT, buyer TEXT, seller TEXT,
            close_date TEXT, days_to_close INTEGER, timeline TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS item (
            txn_id TEXT, key TEXT, label TEXT, expected REAL, actual REAL)"""
    )
    # Additive migration: resolution log for reconciliation exceptions.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS resolution (
            txn_id TEXT, exception_id TEXT, resolved_by TEXT, resolved_at TEXT,
            note TEXT, PRIMARY KEY (txn_id, exception_id))"""
    )
    if fresh:
        for t in SEED:
            conn.execute(
                "INSERT INTO txn VALUES (?,?,?,?,?,?,?)",
                (t["id"], t["property"], t["buyer"], t["seller"],
                 t["close_date"], t["days_to_close"], t["timeline"]),
            )
            for key, label, exp, act in t["items"]:
                conn.execute(
                    "INSERT INTO item VALUES (?,?,?,?,?)",
                    (t["id"], key, label, exp, act),
                )
        conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------
def severity_for(key, variance):
    av = abs(variance)
    if key == "earnest" and variance != 0:
        return "critical"
    if av >= 2000:
        return "high"
    if av >= 500:
        return "medium"
    return "low"


def load_txn(conn, txn_id):
    row = conn.execute("SELECT * FROM txn WHERE id=?", (txn_id,)).fetchone()
    if not row:
        return None
    cols = [c[0] for c in conn.execute("SELECT * FROM txn WHERE id=?", (txn_id,)).description]
    txn = dict(zip(cols, row))
    items = []
    for r in conn.execute(
        "SELECT key,label,expected,actual FROM item WHERE txn_id=?", (txn_id,)
    ):
        items.append({"key": r[0], "label": r[1], "expected": r[2], "actual": r[3]})
    txn["items"] = items
    return txn


def load_resolutions(conn, txn_id):
    """Return {exception_id: {resolved_by, resolved_at, note}} for a txn."""
    out = {}
    for r in conn.execute(
        "SELECT exception_id,resolved_by,resolved_at,note FROM resolution WHERE txn_id=?",
        (txn_id,),
    ):
        out[r[0]] = {"resolved_by": r[1], "resolved_at": r[2], "note": r[3]}
    return out


def reconcile(txn, resolutions=None):
    """Detect exceptions. `resolutions` maps exception_id -> resolution record;
    resolved exceptions are flagged but excluded from the open out-of-balance.
    exception_id is the stable line-item key so it survives across requests."""
    resolutions = resolutions or {}
    exceptions = []
    out_of_balance = 0.0
    open_count = 0
    for it in txn["items"]:
        var = it["actual"] - it["expected"]
        if abs(var) > 0.005:
            res = resolutions.get(it["key"])
            if not res:
                out_of_balance += abs(var)
                open_count += 1
            exceptions.append({
                "id": it["key"],
                "key": it["key"],
                "type": EXC_TYPE.get(it["key"], "FEE_DISCREPANCY"),
                "label": it["label"],
                "expected": it["expected"],
                "actual": it["actual"],
                "variance": var,
                "severity": severity_for(it["key"], var),
                "resolved": bool(res),
                "resolution": res,
            })
    if not exceptions:
        status = "Cleared" if txn["timeline"].endswith("funded") else "Balanced"
    elif open_count == 0:
        # every exception has been resolved
        status = "Cleared" if txn["timeline"].endswith("funded") else "Balanced"
    else:
        status = "Exceptions"
    return status, out_of_balance, exceptions


def txn_summary(conn):
    out = []
    for r in conn.execute("SELECT id FROM txn ORDER BY id"):
        txn = load_txn(conn, r[0])
        res = load_resolutions(conn, r[0])
        status, oob, excs = reconcile(txn, res)
        open_excs = sum(1 for e in excs if not e["resolved"])
        out.append({
            "id": txn["id"],
            "property": txn["property"],
            "buyer": txn["buyer"],
            "seller": txn["seller"],
            "close_date": txn["close_date"],
            "days_to_close": txn["days_to_close"],
            "status": status,
            "out_of_balance": round(oob, 2),
            "exception_count": open_excs,
            "milestone": txn["timeline"].split(",")[-1],
        })
    return out


def portfolio_intel(conn):
    summaries = txn_summary(conn)
    days = [s["days_to_close"] for s in summaries]
    avg_days = round(sum(days) / len(days), 1) if days else 0
    type_counts = {}
    total_oob = 0.0
    with_exc = 0
    for r in conn.execute("SELECT id FROM txn"):
        txn = load_txn(conn, r[0])
        res = load_resolutions(conn, r[0])
        _, oob, excs = reconcile(txn, res)
        total_oob += oob
        open_excs = [e for e in excs if not e["resolved"]]
        if open_excs:
            with_exc += 1
        for e in open_excs:
            type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1
    common = max(type_counts.items(), key=lambda kv: kv[1])[0] if type_counts else "None"
    return {
        "avg_time_to_close": avg_days,
        "total_transactions": len(summaries),
        "with_exceptions": with_exc,
        "most_common_exception": common,
        "exception_type_counts": type_counts,
        "total_out_of_balance": round(total_oob, 2),
        "cleared": sum(1 for s in summaries if s["status"] == "Cleared"),
    }


# --------------------------------------------------------------------------
# Funding readiness + closing packet
# --------------------------------------------------------------------------
def item_by_key(txn, key):
    return next((i for i in txn["items"] if i["key"] == key), None)


def readiness(txn, resolutions=None):
    """Derive a funding-readiness checklist + overall clear-to-fund verdict."""
    resolutions = resolutions or {}
    reached = txn["timeline"].split(",")
    checks = []

    earnest = item_by_key(txn, "earnest")
    earnest_ok = bool(earnest) and earnest["actual"] >= earnest["expected"] - 0.005
    checks.append({
        "key": "earnest_received",
        "label": "Earnest money received in escrow",
        "pass": earnest_ok,
        "detail": (f"Received {money(earnest['actual'])} of "
                   f"{money(earnest['expected'])} expected"
                   if earnest else "No earnest line item"),
    })

    appraisal_ok = "appraisal" in reached
    checks.append({
        "key": "appraisal_cleared",
        "label": "Appraisal cleared",
        "pass": appraisal_ok,
        "detail": "Appraisal milestone reached" if appraisal_ok
                  else "Appraisal milestone not yet reached",
    })

    title_ok = "clear" in reached
    checks.append({
        "key": "title_clear",
        "label": "Title clear to close",
        "pass": title_ok,
        "detail": "Clear-to-close milestone reached" if title_ok
                  else "Clear-to-close milestone not yet reached",
    })

    comm = item_by_key(txn, "commission")
    comm_var = (comm["actual"] - comm["expected"]) if comm else 0.0
    comm_resolved = "commission" in resolutions
    comm_ok = (abs(comm_var) <= 0.005) or comm_resolved
    checks.append({
        "key": "commissions_balanced",
        "label": "Commissions balanced",
        "pass": comm_ok,
        "detail": ("Balanced" if abs(comm_var) <= 0.005
                   else (f"Resolved (variance {money(comm_var)})" if comm_resolved
                         else f"Off by {money(comm_var)}")),
    })

    open_excs = 0
    for it in txn["items"]:
        var = it["actual"] - it["expected"]
        if abs(var) > 0.005 and it["key"] not in resolutions:
            open_excs += 1
    docs_ok = open_excs == 0
    checks.append({
        "key": "docs_complete",
        "label": "Documentation complete (no open exceptions)",
        "pass": docs_ok,
        "detail": ("All exceptions resolved" if docs_ok
                   else f"{open_excs} open exception(s) outstanding"),
    })

    clear_to_fund = all(c["pass"] for c in checks)
    passed = sum(1 for c in checks if c["pass"])
    return {
        "txn_id": txn["id"],
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "clear_to_fund": clear_to_fund,
        "verdict": "CLEAR TO FUND" if clear_to_fund else "NOT CLEAR TO FUND",
    }


def packet_data(conn, txn_id):
    """Assemble everything the closing packet needs."""
    txn = load_txn(conn, txn_id)
    if not txn:
        return None
    res = load_resolutions(conn, txn_id)
    status, oob, excs = reconcile(txn, res)
    excs.sort(key=lambda e: {"critical": 0, "high": 1, "medium": 2, "low": 3}[e["severity"]])
    milestones = ["earnest", "inspection", "appraisal", "clear", "funded"]
    reached = txn["timeline"].split(",")
    statement = []
    for it in txn["items"]:
        var = it["actual"] - it["expected"]
        statement.append({
            "key": it["key"], "label": it["label"],
            "expected": it["expected"], "actual": it["actual"],
            "variance": round(var, 2), "mismatch": abs(var) > 0.005,
        })
    return {
        "id": txn["id"],
        "property": txn["property"],
        "buyer": txn["buyer"],
        "seller": txn["seller"],
        "close_date": txn["close_date"],
        "days_to_close": txn["days_to_close"],
        "status": status,
        "out_of_balance": round(oob, 2),
        "statement": statement,
        "exceptions": excs,
        "milestones": [
            {"name": m, "reached": m in reached} for m in milestones
        ],
        "readiness": readiness(txn, res),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _fmt_money(n):
    return "$" + "{:,.2f}".format(float(n))


def closing_packet_html(pk):
    """Report-grade, print-ready HTML for the closing packet."""
    p = pk
    rows = ""
    for s in p["statement"]:
        v = s["variance"]
        vtxt = "—" if abs(v) < 0.005 else (("+" if v > 0 else "") + _fmt_money(v))
        cls = "miss" if s["mismatch"] else ""
        vcls = "vok" if abs(v) < 0.005 else "vbad"
        rows += (f'<tr class="{cls}"><td>{s["label"]}</td>'
                 f'<td class="num">{_fmt_money(s["expected"])}</td>'
                 f'<td class="num">{_fmt_money(s["actual"])}</td>'
                 f'<td class="num {vcls}">{vtxt}</td></tr>')

    if p["exceptions"]:
        exc_rows = ""
        for e in p["exceptions"]:
            st = "RESOLVED" if e["resolved"] else "OPEN"
            stcls = "ok" if e["resolved"] else "bad"
            note = ""
            if e["resolved"] and e.get("resolution"):
                r = e["resolution"]
                note = (f'<div class="note">Resolved by {r["resolved_by"]} '
                        f'on {r["resolved_at"]}'
                        + (f' — {r["note"]}' if r.get("note") else "") + '</div>')
            exc_rows += (
                f'<tr><td>{e["label"]}<br><span class="sub">'
                f'{e["type"].replace("_", " ")}</span>{note}</td>'
                f'<td class="num">{_fmt_money(e["expected"])}</td>'
                f'<td class="num">{_fmt_money(e["actual"])}</td>'
                f'<td class="num">{("+" if e["variance"] > 0 else "") + _fmt_money(e["variance"])}</td>'
                f'<td><span class="pill {e["severity"]}">{e["severity"].upper()}</span></td>'
                f'<td><span class="pill {stcls}">{st}</span></td></tr>')
        exc_block = (
            '<table class="tbl"><thead><tr><th>Exception</th><th class="num">Expected</th>'
            '<th class="num">Actual</th><th class="num">Variance</th><th>Severity</th>'
            f'<th>Status</th></tr></thead><tbody>{exc_rows}</tbody></table>')
    else:
        exc_block = '<p class="none">No reconciliation exceptions detected.</p>'

    tl = ""
    for m in p["milestones"]:
        lbl = "Clear-to-Close" if m["name"] == "clear" else m["name"].capitalize()
        mark = "✓" if m["reached"] else "○"
        cls = "done" if m["reached"] else "pending"
        tl += f'<li class="{cls}"><span class="mk">{mark}</span> {lbl}</li>'

    rd = p["readiness"]
    rd_rows = ""
    for c in rd["checks"]:
        mark = "PASS" if c["pass"] else "FAIL"
        cls = "ok" if c["pass"] else "bad"
        rd_rows += (f'<tr><td>{c["label"]}<br><span class="sub">{c["detail"]}</span></td>'
                    f'<td><span class="pill {cls}">{mark}</span></td></tr>')
    verdict_cls = "ok" if rd["clear_to_fund"] else "bad"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Closing Packet — {p['id']}</title>
<style>
@page {{ margin: 22mm 18mm; }}
*{{box-sizing:border-box}}
body{{font-family:'Georgia','Times New Roman',serif;color:#1e293b;margin:0;
  background:#f8fafc;line-height:1.5}}
.sheet{{max-width:900px;margin:0 auto;background:#fff;padding:40px 46px;
  box-shadow:0 6px 30px rgba(0,0,0,.08)}}
.letterhead{{display:flex;align-items:center;gap:16px;border-bottom:3px solid #eab308;
  padding-bottom:16px;margin-bottom:8px}}
.mark{{width:52px;height:52px;border-radius:12px;display:grid;place-items:center;
  background:linear-gradient(135deg,#eab308,#a16207);color:#0a0f1a;font-weight:800;
  font-size:22px;font-family:Arial,sans-serif}}
.lh-t{{font-size:24px;font-weight:800;letter-spacing:.5px;font-family:Arial,sans-serif}}
.lh-t span{{color:#a16207}}
.lh-s{{font-size:12px;color:#64748b;letter-spacing:1px;text-transform:uppercase;
  font-family:Arial,sans-serif}}
.doct{{text-align:right;margin-left:auto;font-family:Arial,sans-serif}}
.doct .big{{font-size:15px;font-weight:700;letter-spacing:2px;color:#334155}}
.doct .sm{{font-size:11px;color:#64748b}}
h2{{font-family:Arial,sans-serif;font-size:13px;letter-spacing:2px;text-transform:uppercase;
  color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:6px;margin:30px 0 14px}}
.hdr-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px 32px;font-size:14px;margin-top:14px}}
.hdr-grid .k{{color:#64748b;font-family:Arial,sans-serif;font-size:12px}}
.hdr-grid .row{{display:flex;justify-content:space-between;border-bottom:1px dotted #e2e8f0;padding:5px 0}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px;font-family:Arial,sans-serif}}
.tbl th{{text-align:left;background:#1e293b;color:#e2e8f0;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.8px;padding:8px 10px}}
.tbl th.num,.tbl td.num{{text-align:right}}
.tbl td{{padding:8px 10px;border-bottom:1px solid #eef2f6}}
.tbl tr.miss{{background:#fef2f2}}
.tbl .vbad{{color:#b91c1c;font-weight:700}}
.tbl .vok{{color:#15803d}}
.sub{{font-size:11px;color:#64748b}}
.note{{font-size:11px;color:#475569;font-style:italic;margin-top:3px}}
.pill{{display:inline-block;font-size:10px;font-weight:800;padding:3px 9px;border-radius:10px;
  font-family:Arial,sans-serif;letter-spacing:.5px}}
.pill.ok{{background:#dcfce7;color:#15803d}}
.pill.bad{{background:#fee2e2;color:#b91c1c}}
.pill.critical{{background:#fee2e2;color:#b91c1c}}
.pill.high{{background:#ffedd5;color:#c2410c}}
.pill.medium{{background:#fef9c3;color:#a16207}}
.pill.low{{background:#e2e8f0;color:#475569}}
.tl{{list-style:none;padding:0;margin:14px 0;display:flex;flex-wrap:wrap;gap:8px}}
.tl li{{font-size:12.5px;font-family:Arial,sans-serif;padding:8px 14px;border-radius:8px;
  border:1px solid #e2e8f0}}
.tl li.done{{background:#fffbeb;border-color:#eab308;color:#a16207;font-weight:700}}
.tl li.pending{{color:#94a3b8}}
.tl .mk{{font-weight:800}}
.summary{{display:flex;gap:24px;font-family:Arial,sans-serif;font-size:13px;margin-top:6px}}
.summary .box{{flex:1;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px}}
.summary .box .lbl{{font-size:10.5px;text-transform:uppercase;letter-spacing:.8px;color:#64748b}}
.summary .box .val{{font-size:18px;font-weight:800;margin-top:4px}}
.verdict{{font-family:Arial,sans-serif;text-align:center;margin:18px 0;padding:16px;
  border-radius:12px;font-size:18px;font-weight:800;letter-spacing:1.5px}}
.verdict.ok{{background:#dcfce7;color:#15803d;border:2px solid #22c55e}}
.verdict.bad{{background:#fee2e2;color:#b91c1c;border:2px solid #f43f5e}}
.none{{color:#15803d;font-family:Arial,sans-serif;font-size:13px}}
.signoff{{margin-top:36px;display:grid;grid-template-columns:1fr 1fr;gap:40px}}
.sig{{font-family:Arial,sans-serif}}
.sig .line{{border-bottom:1.5px solid #334155;height:34px;margin-bottom:6px}}
.sig .cap{{font-size:11px;color:#64748b}}
.foot{{margin-top:40px;border-top:1px solid #e2e8f0;padding-top:12px;font-size:10.5px;
  color:#94a3b8;font-family:Arial,sans-serif;display:flex;justify-content:space-between}}
.print-btn{{position:fixed;top:16px;right:16px;background:#eab308;color:#0a0f1a;border:0;
  padding:10px 18px;border-radius:8px;font-weight:700;font-family:Arial,sans-serif;
  cursor:pointer;font-size:13px}}
@media print{{.print-btn{{display:none}} body{{background:#fff}} .sheet{{box-shadow:none}}}}
</style></head><body>
<button class="print-btn" onclick="window.print()">Print / Save PDF</button>
<div class="sheet">
  <div class="letterhead">
    <div class="mark">CL</div>
    <div>
      <div class="lh-t">Close<span>Loop</span></div>
      <div class="lh-s">Escrow &amp; Settlement Services</div>
    </div>
    <div class="doct">
      <div class="big">CLOSING PACKET</div>
      <div class="sm">Confidential Settlement Record</div>
    </div>
  </div>

  <h2>Transaction Summary</h2>
  <div class="hdr-grid">
    <div class="row"><span class="k">Transaction ID</span><span>{p['id']}</span></div>
    <div class="row"><span class="k">Status</span><span>{p['status']}</span></div>
    <div class="row"><span class="k">Property</span><span>{p['property']}</span></div>
    <div class="row"><span class="k">Close Date</span><span>{p['close_date']}</span></div>
    <div class="row"><span class="k">Buyer</span><span>{p['buyer']}</span></div>
    <div class="row"><span class="k">Seller</span><span>{p['seller']}</span></div>
    <div class="row"><span class="k">Days to Close</span><span>{p['days_to_close']}</span></div>
    <div class="row"><span class="k">Out of Balance</span><span>{_fmt_money(p['out_of_balance'])}</span></div>
  </div>

  <h2>Reconciled Settlement Statement</h2>
  <table class="tbl"><thead><tr><th>Line Item</th><th class="num">Expected</th>
    <th class="num">Actual</th><th class="num">Variance</th></tr></thead>
    <tbody>{rows}</tbody></table>

  <h2>Exception List &amp; Resolution</h2>
  {exc_block}

  <h2>Milestone / Close Timeline</h2>
  <ul class="tl">{tl}</ul>

  <h2>Funding Readiness</h2>
  <table class="tbl"><tbody>{rd_rows}</tbody></table>
  <div class="verdict {verdict_cls}">{rd['verdict']} &nbsp;·&nbsp; {rd['passed']} / {rd['total']} checks passed</div>

  <h2>Sign-Off</h2>
  <div class="signoff">
    <div class="sig"><div class="line"></div><div class="cap">Closing Coordinator — signature &amp; date</div></div>
    <div class="sig"><div class="line"></div><div class="cap">Escrow Officer — signature &amp; date</div></div>
    <div class="sig"><div class="line"></div><div class="cap">Buyer — signature &amp; date</div></div>
    <div class="sig"><div class="line"></div><div class="cap">Seller — signature &amp; date</div></div>
  </div>

  <div class="foot">
    <span>Generated {p['generated_at']} · CloseLoop Escrow Reconciliation Console</span>
    <span>Document {p['id']}-CP</span>
  </div>
</div>
</body></html>"""


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            try:
                with open(INDEX_PATH, "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        conn = sqlite3.connect(DB_PATH)
        try:
            if path == "/api/health":
                self._json({"ok": True, "ai": "live" if API_KEY else "mock", "port": PORT})
                return
            if path == "/api/transactions":
                self._json({"transactions": txn_summary(conn)})
                return
            if path == "/api/portfolio":
                self._json(portfolio_intel(conn))
                return
            m = re.match(r"^/api/packet_data/([\w-]+)$", path)
            if m:
                pk = packet_data(conn, m.group(1))
                if not pk:
                    self._json({"error": "not found"}, 404)
                    return
                self._json(pk)
                return
            m = re.match(r"^/closing-packet/([\w-]+)$", path)
            if m:
                pk = packet_data(conn, m.group(1))
                if not pk:
                    self.send_error(404)
                    return
                body = closing_packet_html(pk).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header(
                    "content-disposition",
                    f'inline; filename="closing-packet-{m.group(1)}.html"')
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            m = re.match(r"^/api/readiness/([\w-]+)$", path)
            if m:
                txn = load_txn(conn, m.group(1))
                if not txn:
                    self._json({"error": "not found"}, 404)
                    return
                res = load_resolutions(conn, m.group(1))
                self._json(readiness(txn, res))
                return
            m = re.match(r"^/api/transaction/([\w-]+)$", path)
            if m:
                txn = load_txn(conn, m.group(1))
                if not txn:
                    self._json({"error": "not found"}, 404)
                    return
                res = load_resolutions(conn, m.group(1))
                status, oob, excs = reconcile(txn, res)
                milestones = ["earnest", "inspection", "appraisal", "clear", "funded"]
                reached = txn["timeline"].split(",")
                money_state = {
                    "earnest": "held" if "earnest" in reached else "pending",
                    "inspection": "n/a",
                    "appraisal": "n/a",
                    "clear": "cleared" if "clear" in reached else "pending",
                    "funded": "disbursed" if "funded" in reached else "pending",
                }
                # earnest money-state reflects the actual deposit
                earnest_item = next((i for i in txn["items"] if i["key"] == "earnest"), None)
                if earnest_item and earnest_item["actual"] < earnest_item["expected"]:
                    money_state["earnest"] = "MISSING"
                ledger = []
                for it in txn["items"]:
                    var = it["actual"] - it["expected"]
                    ledger.append({
                        **it,
                        "variance": round(var, 2),
                        "mismatch": abs(var) > 0.005,
                        "resolved": it["key"] in res,
                    })
                self._json({
                    "id": txn["id"],
                    "property": txn["property"],
                    "buyer": txn["buyer"],
                    "seller": txn["seller"],
                    "close_date": txn["close_date"],
                    "days_to_close": txn["days_to_close"],
                    "status": status,
                    "out_of_balance": round(oob, 2),
                    "ledger": ledger,
                    "milestones": [
                        {"name": m, "reached": m in reached, "money_state": money_state[m]}
                        for m in milestones
                    ],
                    "exception_count": sum(1 for e in excs if not e["resolved"]),
                })
                return
            self.send_error(404)
        finally:
            conn.close()

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if path == "/api/reconcile":
            txn_id = data.get("txn_id")
            conn = sqlite3.connect(DB_PATH)
            try:
                txn = load_txn(conn, txn_id)
                if not txn:
                    self._json({"error": "not found"}, 404)
                    return
                res = load_resolutions(conn, txn_id)
                status, oob, excs = reconcile(txn, res)
                for e in excs:
                    e["ai"] = claude_explain(e, txn)
                excs.sort(key=lambda e: (e["resolved"],
                    {"critical": 0, "high": 1, "medium": 2, "low": 3}[e["severity"]]))
                self._json({
                    "txn_id": txn_id,
                    "status": status,
                    "out_of_balance": round(oob, 2),
                    "exceptions": excs,
                    "ai_mode": "live" if API_KEY else "mock",
                })
            finally:
                conn.close()
            return
        if path == "/api/resolve":
            txn_id = data.get("txn_id")
            exception_id = data.get("exception_id")
            note = (data.get("note") or "").strip()
            resolved_by = (data.get("resolved_by") or "Closing Coordinator").strip()
            if not txn_id or not exception_id:
                self._json({"error": "txn_id and exception_id required"}, 400)
                return
            conn = sqlite3.connect(DB_PATH)
            try:
                txn = load_txn(conn, txn_id)
                if not txn:
                    self._json({"error": "not found"}, 404)
                    return
                # exception_id must correspond to a real out-of-balance line item
                item = item_by_key(txn, exception_id)
                if not item or abs(item["actual"] - item["expected"]) <= 0.005:
                    self._json({"error": "no such open exception"}, 404)
                    return
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                conn.execute(
                    """INSERT INTO resolution (txn_id, exception_id, resolved_by,
                       resolved_at, note) VALUES (?,?,?,?,?)
                       ON CONFLICT(txn_id, exception_id) DO UPDATE SET
                       resolved_by=excluded.resolved_by,
                       resolved_at=excluded.resolved_at, note=excluded.note""",
                    (txn_id, exception_id, resolved_by, now, note),
                )
                conn.commit()
                res = load_resolutions(conn, txn_id)
                status, oob, excs = reconcile(txn, res)
                self._json({
                    "txn_id": txn_id,
                    "exception_id": exception_id,
                    "resolved_by": resolved_by,
                    "resolved_at": now,
                    "note": note,
                    "status": status,
                    "out_of_balance": round(oob, 2),
                    "open_exceptions": sum(1 for e in excs if not e["resolved"]),
                })
            finally:
                conn.close()
            return
        self.send_error(404)


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"CloseLoop on http://localhost:{PORT}  (AI: {'live' if API_KEY else 'mock'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
