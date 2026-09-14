#!/usr/bin/env python3
"""CompliAgent - a living compliance radar for regulated SMBs.

Python 3 stdlib only. http.server on PORT 8808, single-file index.html,
sqlite3 seeded on first run. Optional real Claude call if
/home/sagar-uniyal/creator-os/.env has ANTHROPIC_API_KEY, else MOCK.
"""
import json
import os
import re
import sqlite3
import ssl
import urllib.request
import urllib.error
from datetime import date, timedelta, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8808
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "compliagent.db")
INDEX = os.path.join(HERE, "index.html")
ENV_PATH = "/home/sagar-uniyal/creator-os/.env"
MODEL = "claude-sonnet-4-6"

TODAY = date(2026, 9, 14)


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


# ---------------------------------------------------------------- seed data
BUSINESS = {
    "name": "Meridian Health Data Co.",
    "industry": "Digital Health / Health-Tech SaaS",
    "jurisdictions": ["EU", "UK", "US-CA", "US-Federal", "UAE"],
    "headcount": 68,
    "handles_pii": True,
    "handles_payments": True,
    "regulated_data": "Protected health information (PHI) + EU personal data",
}

# domain -> color handled on client. Domains: data-privacy, AML, employment, tax, security, sector
REGS = [
    ("EU AI Act - High-Risk System Obligations", "EU", "2026-08-02", "security",
     "Providers of high-risk AI systems must maintain risk management, logging, human oversight and post-market monitoring.", "high"),
    ("GDPR Enforcement: DPIA for Health Data", "EU", "2026-10-01", "data-privacy",
     "Renewed guidance requiring Data Protection Impact Assessments for large-scale processing of special-category health data.", "high"),
    ("UK DPDI Act - Records of Processing", "UK", "2026-11-15", "data-privacy",
     "New UK data reform replaces ROPA with a proportionate 'records of high-risk processing' regime.", "medium"),
    ("California Delete Act (DROP) Registration", "US-CA", "2026-09-30", "data-privacy",
     "Data brokers must register with the CPPA and honor deletion requests via the DROP mechanism.", "high"),
    ("HIPAA Security Rule Update - MFA Mandate", "US-Federal", "2026-12-31", "security",
     "Proposed rule mandates multi-factor authentication and encryption at rest for all ePHI systems.", "high"),
    ("EU DORA - ICT Third-Party Risk", "EU", "2027-01-17", "security",
     "Digital Operational Resilience Act extends ICT third-party risk register and incident reporting to in-scope firms.", "medium"),
    ("UAE PDPL Executive Regulations", "UAE", "2026-10-20", "data-privacy",
     "Executive regulations operationalize consent, cross-border transfer and breach-notification duties under UAE PDPL.", "medium"),
    ("6AMLD - Expanded Predicate Offenses", "EU", "2027-02-01", "AML",
     "Sixth AML Directive broadens criminal liability and requires enhanced customer due diligence for payment flows.", "low"),
    ("US Corporate Transparency Act - BOI", "US-Federal", "2026-09-25", "AML",
     "Beneficial Ownership Information reporting to FinCEN for covered reporting companies.", "high"),
    ("EU Pay Transparency Directive", "EU", "2027-06-07", "employment",
     "Employers must report gender pay gaps and provide pay-range transparency to candidates.", "low"),
    ("UK Worker Protection Act - Harassment Duty", "UK", "2026-10-26", "employment",
     "Proactive duty on employers to prevent sexual harassment, including third-party harassment.", "medium"),
    ("OECD Pillar Two - Global Minimum Tax", "EU", "2027-01-01", "tax",
     "15% global minimum effective tax rate for in-scope multinational groups; top-up tax filings.", "low"),
    ("US-CA CPRA - Cybersecurity Audit Rule", "US-CA", "2026-11-30", "security",
     "Businesses processing high volumes of personal info must complete annual independent cybersecurity audits.", "medium"),
    ("EU NIS2 - Incident Reporting", "EU", "2026-10-17", "security",
     "Essential/important entities must report significant incidents within 24h early warning + 72h notification.", "high"),
    ("PCI DSS 4.0 - Mandatory Controls", "US-Federal", "2026-12-31", "security",
     "Future-dated PCI DSS 4.0 requirements (e.g. targeted risk analyses, authenticated scans) become mandatory.", "medium"),
]

# obligations derived from regs: (reg_index, title, domain, status, due_offset_days)
OBLIGATIONS = [
    (0, "Establish AI risk-management file for triage model", "security", "In-progress", -3),
    (1, "Complete DPIA for PHI analytics pipeline", "data-privacy", "Open", 17),
    (3, "Register as data broker with CPPA (DROP)", "data-privacy", "Open", 16),
    (4, "Roll out MFA across ePHI systems", "security", "In-progress", 108),
    (8, "File Beneficial Ownership Information with FinCEN", "AML", "Open", 11),
    (13, "Stand up 24h/72h incident-reporting runbook", "security", "Open", 33),
    (6, "Update cross-border transfer clauses (UAE PDPL)", "data-privacy", "Open", 36),
    (2, "Migrate ROPA to UK high-risk processing records", "data-privacy", "Done", -20),
    (10, "Publish anti-harassment policy + training", "employment", "In-progress", 42),
    (12, "Scope annual cybersecurity audit (CPRA)", "security", "Open", 77),
    (5, "Build ICT third-party risk register (DORA)", "security", "Open", 125),
    (14, "Run authenticated PCI scans (v4.0)", "security", "Done", -8),
]


def init_db():
    con = sqlite3.connect(DB)
    c = con.cursor()
    c.execute("""CREATE TABLE business (id INTEGER PRIMARY KEY, name TEXT, industry TEXT,
                 jurisdictions TEXT, headcount INT, profile TEXT)""")
    c.execute("""CREATE TABLE regs (id INTEGER PRIMARY KEY, title TEXT, jurisdiction TEXT,
                 effective TEXT, domain TEXT, summary TEXT, severity TEXT)""")
    c.execute("""CREATE TABLE obligations (id INTEGER PRIMARY KEY, reg_id INT, title TEXT,
                 domain TEXT, status TEXT, due TEXT)""")
    c.execute("INSERT INTO business VALUES (1,?,?,?,?,?)",
              (BUSINESS["name"], BUSINESS["industry"], ",".join(BUSINESS["jurisdictions"]),
               BUSINESS["headcount"], json.dumps(BUSINESS)))
    for i, (title, juris, eff, domain, summ, sev) in enumerate(REGS):
        c.execute("INSERT INTO regs VALUES (?,?,?,?,?,?,?)",
                  (i, title, juris, eff, domain, summ, sev))
    for oid, (ri, title, domain, status, off) in enumerate(OBLIGATIONS):
        due = (TODAY + timedelta(days=off)).isoformat()
        c.execute("INSERT INTO obligations VALUES (?,?,?,?,?,?)",
                  (oid, ri, title, domain, status, due))
    con.commit()
    con.close()


def migrate_db(con):
    """Additive migration: add auto-file columns + autopilot log if missing. Safe to run repeatedly."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(obligations)").fetchall()}
    if "filed_at" not in cols:
        con.execute("ALTER TABLE obligations ADD COLUMN filed_at TEXT")
    if "confirmation" not in cols:
        con.execute("ALTER TABLE obligations ADD COLUMN confirmation TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS autopilot_log (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, obligation_id INT,
                 obligation TEXT, action TEXT, confirmation TEXT, justification TEXT)""")
    con.commit()


def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    migrate_db(con)
    return con


# ---------------------------------------------------------------- data api
def api_bootstrap():
    con = get_db()
    biz = json.loads(con.execute("SELECT profile FROM business WHERE id=1").fetchone()[0])
    regs = [dict(r) for r in con.execute("SELECT * FROM regs").fetchall()]
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    con.close()
    for r in regs:
        eff = date.fromisoformat(r["effective"])
        r["days_to_effective"] = (eff - TODAY).days
    score, stats = compute_score(obs)
    total = stats["total"]
    done = stats["done"]
    upcoming = sorted([o for o in obs if o["status"] != "Done"],
                      key=lambda o: o["due"])
    next_deadline = upcoming[0] if upcoming else None
    # by domain
    by_domain = {}
    for o in obs:
        d = by_domain.setdefault(o["domain"], {"open": 0, "in_progress": 0, "done": 0})
        key = {"Open": "open", "In-progress": "in_progress", "Done": "done"}[o["status"]]
        d[key] += 1
    return {
        "today": TODAY.isoformat(),
        "business": biz,
        "regs": regs,
        "obligations": obs,
        "score": score,
        "stats": stats,
        "by_domain": by_domain,
        "next_deadline": next_deadline,
        "ai_mode": "LIVE (claude-sonnet-4-6)" if API_KEY else "MOCK",
    }


def set_status(oid, status):
    con = get_db()
    con.execute("UPDATE obligations SET status=? WHERE id=?", (status, oid))
    con.commit()
    con.close()


# ---------------------------------------------------------------- Claude call
def call_claude(reg, biz):
    prompt = (
        "You are CompliAgent, a compliance analyst. Given a regulatory change and a specific "
        "business profile, explain in plain English (a) whether and why it applies to THIS "
        "business, and (b) 2-4 concrete action items. Be specific, cite the reg. Keep under 160 words.\n\n"
        f"BUSINESS PROFILE:\n{json.dumps(biz, indent=2)}\n\n"
        f"REGULATORY CHANGE:\nTitle: {reg['title']}\nJurisdiction: {reg['jurisdiction']}\n"
        f"Effective: {reg['effective']}\nDomain: {reg['domain']}\nSummary: {reg['summary']}\n\n"
        "Respond as JSON: {\"applies\": true/false, \"applicability\": \"...\", "
        "\"actions\": [\"...\", \"...\"]}"
    )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": API_KEY,
                 "anthropic-version": "2023-06-01"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=45, context=ctx) as resp:
        data = json.loads(resp.read())
    text = "".join(b.get("text", "") for b in data.get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
            parsed["source"] = "claude-sonnet-4-6"
            return parsed
        except json.JSONDecodeError:
            pass
    return {"applies": True, "applicability": text.strip(), "actions": [],
            "source": "claude-sonnet-4-6"}


def mock_assess(reg, biz):
    juris = reg["jurisdiction"]
    applies = juris in biz["jurisdictions"] or juris in ("EU", "US-Federal")
    domain = reg["domain"]
    dstr = {
        "data-privacy": "processes special-category health data (PHI) and EU personal data",
        "security": "operates ePHI systems and payment flows requiring hardened controls",
        "AML": "handles payment flows that trigger due-diligence and reporting duties",
        "employment": f"employs {biz['headcount']} staff across multiple jurisdictions",
        "tax": "operates across EU/US as an in-scope multinational group",
        "sector": "operates in a regulated health-tech sector",
    }.get(domain, "operates in a regulated sector")
    applicability = (
        f"This change applies because {biz['name']} {dstr}, and it is active in {juris}. "
        f"'{reg['title']}' (effective {reg['effective']}) directly touches your "
        f"{domain.replace('-', ' ')} obligations."
        if applies else
        f"Low direct exposure: {biz['name']} has limited nexus to {juris} for this {domain} change, "
        "but monitor for downstream contractual impact."
    )
    actions = {
        "data-privacy": ["Map affected data flows and update your ROPA/DPIA.",
                         "Refresh consent and cross-border transfer clauses.",
                         "Assign a DPO owner and a target completion date."],
        "security": ["Run a gap assessment against the new control set.",
                     "Prioritize MFA/encryption remediation for in-scope systems.",
                     "Draft an incident-reporting runbook with clocked SLAs."],
        "AML": ["Confirm whether you are a covered reporting entity.",
                "Collect beneficial-ownership / KYC data for filing.",
                "Calendar the filing deadline and assign an owner."],
        "employment": ["Publish the required policy and train staff.",
                       "Update HR handbooks and reporting channels.",
                       "Log evidence of the preventive duty."],
        "tax": ["Assess in-scope status with your tax advisor.",
                "Model the top-up tax exposure.",
                "Prepare filing workpapers."],
    }.get(domain, ["Assess applicability with counsel.", "Assign an owner and due date."])
    return {"applies": applies, "applicability": applicability,
            "actions": actions, "source": "mock"}


def api_assess(reg_id):
    con = get_db()
    row = con.execute("SELECT * FROM regs WHERE id=?", (reg_id,)).fetchone()
    biz = json.loads(con.execute("SELECT profile FROM business WHERE id=1").fetchone()[0])
    con.close()
    if not row:
        return {"error": "unknown reg"}
    reg = dict(row)
    if API_KEY:
        try:
            result = call_claude(reg, biz)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ssl.SSLError, OSError) as e:
            result = mock_assess(reg, biz)
            result["source"] = "mock (live call failed: %s)" % type(e).__name__
    else:
        result = mock_assess(reg, biz)
    result["reg"] = reg
    return result


def compute_score(obs):
    """Compliance score: reward done, partial for in-progress, penalize overdue."""
    total = len(obs)
    done = sum(1 for o in obs if o["status"] == "Done")
    inprog = sum(1 for o in obs if o["status"] == "In-progress")
    overdue = sum(1 for o in obs if o["status"] != "Done"
                  and date.fromisoformat(o["due"]) < TODAY)
    if total:
        score = (done + 0.5 * inprog) / total * 100 - overdue * 6
    else:
        score = 100
    score = max(0, min(100, round(score)))
    stats = {"total": total, "done": done, "in_progress": inprog,
             "overdue": overdue, "open": total - done - inprog}
    return score, stats


def gen_confirmation(oid):
    """Deterministic-ish confirmation reference for a filed obligation."""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"CA-{TODAY.year}-{oid:04d}-{stamp[-4:]}"


def api_file(oid):
    """Mark an obligation as auto-filed: status Done + filed timestamp + confirmation ref."""
    con = get_db()
    row = con.execute("SELECT * FROM obligations WHERE id=?", (oid,)).fetchone()
    if not row:
        con.close()
        return {"error": "unknown obligation"}
    filed_at = datetime.now().isoformat(timespec="seconds")
    confirmation = gen_confirmation(oid)
    con.execute("UPDATE obligations SET status='Done', filed_at=?, confirmation=? WHERE id=?",
                (filed_at, confirmation, oid))
    con.commit()
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    con.close()
    score, stats = compute_score(obs)
    filed = next(o for o in obs if o["id"] == oid)
    return {"ok": True, "filed": filed, "confirmation": confirmation,
            "filed_at": filed_at, "obligations": obs, "score": score, "stats": stats}


def gather_audit_pack():
    """Build the structured audit-pack payload used by both JSON + HTML report."""
    con = get_db()
    biz = json.loads(con.execute("SELECT profile FROM business WHERE id=1").fetchone()[0])
    regs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM regs").fetchall()}
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    con.close()
    score, stats = compute_score(obs)
    owners = {"data-privacy": "Data Protection Office", "security": "CISO / Security",
              "AML": "Financial Crime / Compliance", "employment": "People & HR",
              "tax": "Group Tax", "sector": "Regulatory Affairs"}
    items = []
    for o in sorted(obs, key=lambda x: (x["status"] == "Done", x["due"])):
        reg = regs.get(o["reg_id"], {})
        due = date.fromisoformat(o["due"])
        days = (due - TODAY).days
        overdue = o["status"] != "Done" and days < 0
        if o["status"] == "Done":
            evidence = ("Filed " + o["filed_at"][:16].replace("T", " ") +
                        " · ref " + o["confirmation"]) if o.get("filed_at") else \
                       "Completed — evidence booked in compliance vault."
        elif o["status"] == "In-progress":
            evidence = "Work in progress — interim workpapers on file."
        else:
            evidence = "Not started — no evidence booked yet."
        items.append({
            "id": o["id"], "title": o["title"], "domain": o["domain"],
            "status": o["status"], "owner": owners.get(o["domain"], "Compliance"),
            "due": o["due"], "days_to_due": days, "overdue": overdue,
            "filed_at": o.get("filed_at"), "confirmation": o.get("confirmation"),
            "evidence": evidence,
            "source_reg": {
                "title": reg.get("title", "—"), "jurisdiction": reg.get("jurisdiction", "—"),
                "effective": reg.get("effective", "—"), "domain": reg.get("domain", ""),
                "summary": reg.get("summary", ""), "severity": reg.get("severity", ""),
            },
        })
    done = stats["done"]
    total = stats["total"]
    filed_count = sum(1 for o in obs if o.get("filed_at"))
    summary = (
        f"As of {TODAY.isoformat()}, {biz['name']} tracks {len(regs)} regulatory changes "
        f"across {len(biz['jurisdictions'])} jurisdictions, mapped to {total} discrete "
        f"compliance obligations. {done} of {total} are complete ({filed_count} auto-filed with "
        f"registry confirmations), {stats['in_progress']} are in progress and {stats['open']} remain "
        f"open. {stats['overdue']} obligation(s) are past due. The resulting compliance posture "
        f"score is {score}/100. This pack constitutes a point-in-time evidentiary record suitable "
        "for internal audit, board reporting and regulator inquiry."
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": TODAY.isoformat(),
        "business": biz,
        "score": score,
        "stats": stats,
        "reg_count": len(regs),
        "filed_count": filed_count,
        "summary": summary,
        "items": items,
        "reference": f"AUDIT-{TODAY.year}-{stats['done']:02d}{stats['open']:02d}-CA",
    }


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_audit_html(pack):
    biz = pack["business"]
    st = pack["stats"]
    sc = pack["score"]
    sc_col = "#16a34a" if sc >= 75 else "#d97706" if sc >= 50 else "#dc2626"
    rows = []
    for it in pack["items"]:
        stat = it["status"]
        badge = {"Done": ("#16a34a", "#dcfce7"), "In-progress": ("#4f46e5", "#e0e7ff"),
                 "Open": ("#64748b", "#f1f5f9")}.get(stat, ("#64748b", "#f1f5f9"))
        due_txt = _esc(it["due"])
        if it["overdue"]:
            due_txt += f' <span style="color:#dc2626;font-weight:600">({abs(it["days_to_due"])}d overdue)</span>'
        conf = f'<div class="conf">ref {_esc(it["confirmation"])}</div>' if it.get("confirmation") else ""
        rows.append(f"""<tr>
      <td>
        <div class="oblig">{_esc(it['title'])}</div>
        <div class="sub">{_esc(it['owner'])} · {_esc(it['domain'])}</div>
      </td>
      <td><span class="badge" style="color:{badge[0]};background:{badge[1]}">{_esc(stat)}</span>{conf}</td>
      <td>{due_txt}</td>
      <td>
        <div class="regt">{_esc(it['source_reg']['title'])}</div>
        <div class="sub">{_esc(it['source_reg']['jurisdiction'])} · eff {_esc(it['source_reg']['effective'])}</div>
      </td>
      <td class="evi">{_esc(it['evidence'])}</td>
    </tr>""")
    juris = " · ".join(_esc(j) for j in biz["jurisdictions"])
    tiles = f"""
      <div class="tile"><b style="color:{sc_col}">{sc}</b><span>Posture score</span></div>
      <div class="tile"><b>{st['done']}</b><span>Complete</span></div>
      <div class="tile"><b>{st['in_progress']}</b><span>In progress</span></div>
      <div class="tile"><b>{st['open']}</b><span>Open</span></div>
      <div class="tile"><b style="color:{'#dc2626' if st['overdue'] else '#0f172a'}">{st['overdue']}</b><span>Overdue</span></div>
      <div class="tile"><b>{pack['filed_count']}</b><span>Auto-filed</span></div>"""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<title>Compliance Audit Pack · {_esc(biz['name'])}</title>
<style>
:root{{--indigo:#6366f1;--steel:#64748b;--ink:#0f172a;--line:#e2e8f0;}}
*{{box-sizing:border-box}}
body{{margin:0;background:#eef1f6;color:var(--ink);
  font:13.5px/1.6 "Inter",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:32px 0}}
.sheet{{max-width:960px;margin:0 auto;background:#fff;border:1px solid var(--line);
  border-radius:6px;box-shadow:0 8px 40px rgba(15,23,42,.10);overflow:hidden}}
.head{{background:linear-gradient(115deg,#4f46e5,#6366f1 55%,#22d3ee);color:#fff;
  padding:26px 40px;display:flex;align-items:center;gap:18px}}
.mark{{width:44px;height:44px;border-radius:11px;flex:0 0 auto;
  background:conic-gradient(from 210deg,#fff,#c7d2fe,#fff);position:relative;
  box-shadow:0 0 0 4px rgba(255,255,255,.18)}}
.mark::after{{content:"";position:absolute;inset:7px;border-radius:6px;
  background:#4f46e5}}
.head h1{{font-size:20px;margin:0;letter-spacing:.4px}}
.head .k{{font-size:11.5px;opacity:.85;letter-spacing:2px;text-transform:uppercase;margin-bottom:3px}}
.head .meta{{margin-left:auto;text-align:right;font-size:11.5px;opacity:.92;line-height:1.7}}
.head .meta b{{font-size:13px}}
.body{{padding:30px 40px 44px}}
h2{{font-size:12px;letter-spacing:1.6px;text-transform:uppercase;color:var(--indigo);
  border-bottom:2px solid var(--line);padding-bottom:7px;margin:30px 0 14px}}
h2:first-child{{margin-top:0}}
.profile{{display:grid;grid-template-columns:1fr 1fr;gap:6px 26px;font-size:13px}}
.profile .r{{display:flex;gap:10px;padding:5px 0;border-bottom:1px dashed var(--line)}}
.profile .r b{{color:var(--steel);font-weight:600;flex:0 0 140px}}
.tiles{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:6px 0 4px}}
.tile{{border:1px solid var(--line);border-radius:8px;padding:12px 10px;text-align:center;
  background:#f8fafc}}
.tile b{{display:block;font-size:24px;font-weight:700;line-height:1}}
.tile span{{font-size:9.5px;letter-spacing:.6px;text-transform:uppercase;color:var(--steel);
  margin-top:5px;display:block}}
.summary{{background:#f8fafc;border:1px solid var(--line);border-left:3px solid var(--indigo);
  border-radius:6px;padding:14px 18px;font-size:13px;color:#334155}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;color:var(--steel);font-size:10px;letter-spacing:.8px;text-transform:uppercase;
  border-bottom:1.5px solid var(--line);padding:8px 10px}}
td{{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:nth-child(even) td{{background:#fafbfc}}
.oblig{{font-weight:600}}
.sub{{color:var(--steel);font-size:10.5px;margin-top:2px}}
.regt{{font-weight:500}}
.evi{{color:#334155;font-size:11.5px;max-width:210px}}
.badge{{font-size:10px;font-weight:700;letter-spacing:.4px;border-radius:5px;padding:2px 8px;
  display:inline-block}}
.conf{{font-size:9.5px;color:#16a34a;margin-top:4px;font-family:ui-monospace,Menlo,monospace}}
.foot{{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);color:var(--steel);
  font-size:10.5px;display:flex;justify-content:space-between}}
.actions{{max-width:960px;margin:0 auto 14px;text-align:right}}
.actions button{{background:var(--indigo);color:#fff;border:0;border-radius:7px;
  padding:9px 18px;font-size:12.5px;font-weight:600;cursor:pointer}}
@media print{{body{{background:#fff;padding:0}}.sheet{{box-shadow:none;border:0}}.actions{{display:none}}}}
</style></head><body>
<div class="actions"><button onclick="window.print()">Print / Save as PDF</button></div>
<div class="sheet">
  <div class="head">
    <div class="mark"></div>
    <div>
      <div class="k">CompliAgent · Audit Pack</div>
      <h1>{_esc(biz['name'])}</h1>
    </div>
    <div class="meta">
      <div>Ref <b>{_esc(pack['reference'])}</b></div>
      <div>As of {_esc(pack['as_of'])}</div>
      <div>Generated {_esc(pack['generated_at'].replace('T',' '))}</div>
    </div>
  </div>
  <div class="body">
    <h2>Business Profile</h2>
    <div class="profile">
      <div class="r"><b>Legal entity</b><span>{_esc(biz['name'])}</span></div>
      <div class="r"><b>Industry</b><span>{_esc(biz['industry'])}</span></div>
      <div class="r"><b>Jurisdictions</b><span>{juris}</span></div>
      <div class="r"><b>Headcount</b><span>{_esc(biz['headcount'])}</span></div>
      <div class="r"><b>Regulated data</b><span>{_esc(biz.get('regulated_data','—'))}</span></div>
      <div class="r"><b>Handles PII / payments</b><span>{'Yes' if biz.get('handles_pii') else 'No'} / {'Yes' if biz.get('handles_payments') else 'No'}</span></div>
    </div>

    <h2>Compliance Posture</h2>
    <div class="tiles">{tiles}</div>

    <h2>Executive Summary</h2>
    <div class="summary">{_esc(pack['summary'])}</div>

    <h2>Obligations &amp; Evidence Register</h2>
    <table>
      <thead><tr><th>Obligation / Owner</th><th>Status</th><th>Due</th>
        <th>Mapped source reg change</th><th>Evidence</th></tr></thead>
      <tbody>
    {''.join(rows)}
      </tbody>
    </table>

    <div class="foot">
      <span>CompliAgent — generated compliance evidence pack. Point-in-time; not legal advice.</span>
      <span>{_esc(pack['reference'])}</span>
    </div>
  </div>
</div>
</body></html>"""


def api_ambient(reg_id):
    """Simulate the ambient agent auto-filing a renewal before its deadline."""
    con = get_db()
    row = con.execute("SELECT * FROM regs WHERE id=?", (reg_id,)).fetchone()
    con.close()
    reg = dict(row) if row else {"title": "Annual filing", "jurisdiction": "US-Federal"}
    now = datetime(2036, 5, 12, 3, 17, 4)
    steps = [
        f"Detected renewal window for '{reg['title']}' opening in 9 days.",
        "Pulled prior-year filing + current business profile from vault.",
        "Reconciled beneficial-ownership + control changes since last cycle.",
        "Auto-generated filing package; validated against schema v4.",
        f"Filed with {reg['jurisdiction']} registry. Confirmation #CA-2036-{reg_id:04d}-QX.",
        "Booked evidence, updated obligation to Done, scheduled next cycle.",
    ]
    return {"ts": now.isoformat(), "reg": reg, "steps": steps,
            "confirmation": f"CA-2036-{reg_id:04d}-QX"}


# ---------------------------------------------------------------- autopilot
# Safe-actions guardrail: only routine renewals/filings (low/medium severity source
# regs) may be auto-filed hands-free. High-severity source regs are high-stakes and
# get queued for human sign-off rather than filed.
SAFE_SEVERITIES = {"low", "medium"}


def _is_high_stakes(reg):
    return (reg.get("severity") or "").lower() not in SAFE_SEVERITIES


def call_claude_justify(ob, reg, high_stakes):
    """One-line justification for the autopilot's decision. Live Claude or mock fallback."""
    if high_stakes:
        ask = ("justify HOLDING this obligation for human sign-off (do NOT auto-file it), "
               "because its source regulation is high-stakes")
    else:
        ask = ("justify AUTO-FILING this routine compliance obligation hands-free")
    prompt = (
        "You are CompliAgent's autonomous filing autopilot. In ONE concise sentence "
        f"(max 28 words, no preamble), {ask}, referencing the obligation and its source "
        "regulation.\n\n"
        f"OBLIGATION: {ob['title']} (domain: {ob['domain']}, due: {ob['due']})\n"
        f"SOURCE REG: {reg.get('title','—')} — {reg.get('jurisdiction','—')}, "
        f"severity {reg.get('severity','—')}.\n\n"
        "Respond with the sentence only, no quotes."
    )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 120,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": API_KEY,
                 "anthropic-version": "2023-06-01"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=45, context=ctx) as resp:
        data = json.loads(resp.read())
    text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
    return text.strip().strip('"') or mock_justify(ob, reg, high_stakes)


def mock_justify(ob, reg, high_stakes):
    dom = ob['domain'].replace('-', ' ')
    title = reg.get('title', 'source regulation')
    juris = reg.get('jurisdiction', '—')
    if high_stakes:
        return (
            f"High-stakes {dom} obligation under '{title}' ({juris}, "
            f"{reg.get('severity', 'high')} severity) — outside delegated auto-file "
            f"authority; queued for human sign-off before its {ob['due']} deadline."
        )
    return (
        f"Routine {dom} filing under '{title}' ({juris}); low-risk renewal within "
        f"delegated authority, auto-filed ahead of its {ob['due']} deadline with "
        f"evidence booked."
    )


def justify(ob, reg, high_stakes):
    if API_KEY:
        try:
            return call_claude_justify(ob, reg, high_stakes)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ssl.SSLError, OSError):
            return mock_justify(ob, reg, high_stakes)
    return mock_justify(ob, reg, high_stakes)


def log_autopilot(con, oid, title, action, confirmation, justification):
    con.execute(
        "INSERT INTO autopilot_log (ts, obligation_id, obligation, action, confirmation, "
        "justification) VALUES (?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), oid, title, action,
         confirmation, justification))
    con.commit()


def _already_queued_ids(con):
    rows = con.execute(
        "SELECT DISTINCT obligation_id FROM autopilot_log "
        "WHERE action='queued for human approval'").fetchall()
    return {r[0] for r in rows}


def api_autopilot_step():
    """Run one autopilot cycle: pick the nearest-due OPEN obligation. If its source reg
    is safe (routine renewal/filing) auto-file it; if high-stakes, queue for human sign-off."""
    con = get_db()
    regs = {r["id"]: dict(r) for r in con.execute("SELECT * FROM regs").fetchall()}
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    queued = _already_queued_ids(con)
    # candidates: not yet Done and not already parked in the approval queue
    pending = sorted(
        [o for o in obs if o["status"] != "Done" and o["id"] not in queued],
        key=lambda o: o["due"])
    if not pending:
        con.close()
        score, stats = compute_score(obs)
        return {"ok": True, "done": True, "action": "idle",
                "message": "No pending obligations left to process.",
                "score": score, "stats": stats}
    ob = pending[0]
    reg = regs.get(ob["reg_id"], {})
    high_stakes = _is_high_stakes(reg)
    justification = justify(ob, reg, high_stakes)
    if high_stakes:
        log_autopilot(con, ob["id"], ob["title"], "queued for human approval",
                      None, justification)
        obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
        con.close()
        score, stats = compute_score(obs)
        return {"ok": True, "done": False, "action": "queued for human approval",
                "obligation": {"id": ob["id"], "title": ob["title"], "domain": ob["domain"]},
                "reg": {"title": reg.get("title"), "severity": reg.get("severity"),
                        "jurisdiction": reg.get("jurisdiction")},
                "confirmation": None, "justification": justification,
                "score": score, "stats": stats}
    # SAFE: auto-file via the same logic as api_file
    filed_at = datetime.now().isoformat(timespec="seconds")
    confirmation = gen_confirmation(ob["id"])
    con.execute("UPDATE obligations SET status='Done', filed_at=?, confirmation=? WHERE id=?",
                (filed_at, confirmation, ob["id"]))
    con.commit()
    log_autopilot(con, ob["id"], ob["title"], "filed", confirmation, justification)
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    con.close()
    score, stats = compute_score(obs)
    return {"ok": True, "done": False, "action": "filed",
            "obligation": {"id": ob["id"], "title": ob["title"], "domain": ob["domain"]},
            "reg": {"title": reg.get("title"), "severity": reg.get("severity"),
                    "jurisdiction": reg.get("jurisdiction")},
            "confirmation": confirmation, "filed_at": filed_at,
            "justification": justification, "score": score, "stats": stats}


def api_autopilot_log():
    con = get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM autopilot_log ORDER BY id DESC").fetchall()]
    con.close()
    return {"log": rows, "count": len(rows)}


def api_autopilot_state():
    con = get_db()
    obs = [dict(r) for r in con.execute("SELECT * FROM obligations").fetchall()]
    filed_count = con.execute(
        "SELECT COUNT(*) FROM autopilot_log WHERE action='filed'").fetchone()[0]
    queued = _already_queued_ids(con)
    con.close()
    score, stats = compute_score(obs)
    remaining = [o for o in obs if o["status"] != "Done" and o["id"] not in queued]
    return {
        # "running" reflects whether there is still work the autopilot can act on
        "running": len(remaining) > 0,
        "filed_count": filed_count,
        "needs_approval": len(queued),
        "remaining": len(remaining),
        "score": score, "stats": stats,
    }


# ---------------------------------------------------------------- http
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

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, {"error": "index missing"})
        elif self.path == "/api/bootstrap":
            self._send(200, api_bootstrap())
        elif self.path == "/api/health":
            self._send(200, {"ok": True, "ai_mode": "live" if API_KEY else "mock"})
        elif self.path == "/api/audit_pack":
            self._send(200, gather_audit_pack())
        elif self.path == "/api/autopilot/log":
            self._send(200, api_autopilot_log())
        elif self.path == "/api/autopilot/state":
            self._send(200, api_autopilot_state())
        elif self.path == "/audit-pack":
            self._send(200, render_audit_html(gather_audit_pack()),
                       "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if self.path == "/api/assess":
            self._send(200, api_assess(int(payload.get("reg_id", 0))))
        elif self.path == "/api/status":
            set_status(int(payload["id"]), payload["status"])
            self._send(200, {"ok": True})
        elif self.path == "/api/ambient":
            self._send(200, api_ambient(int(payload.get("reg_id", 8))))
        elif self.path == "/api/file":
            self._send(200, api_file(int(payload.get("obligation_id", payload.get("id", 0)))))
        elif self.path == "/api/autopilot/step":
            self._send(200, api_autopilot_step())
        else:
            self._send(404, {"error": "not found"})


def main():
    if not os.path.exists(DB):
        init_db()
        print("[compliagent] seeded db at", DB)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[compliagent] listening on http://0.0.0.0:{PORT}  (AI mode: "
          f"{'LIVE' if API_KEY else 'MOCK'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
