#!/usr/bin/env python3
"""Mesa - the ambient operational brain for the mid-market.

Unifies a company's internal tools into ONE operational graph
(people / projects / work / systems) and lets anyone "ask the company"
and get a grounded answer, then act.

Python 3 stdlib only. http.server + sqlite3. Serves a single-file index.html.
Optional real Claude call (claude-sonnet-4-6 via urllib) if
creator-os/.env has ANTHROPIC_API_KEY; MOCK fallback otherwise.

Part of the ACE family (violet/space theme, mesh-core globe).
"""
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8810
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "mesa.db")
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


def claude_call(prompt, max_tokens=500):
    body = json.dumps(
        {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
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
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()


# --------------------------------------------------------------------------
# Seed data — a small mock mid-market company ("Northwind Labs", ~600 heads)
# --------------------------------------------------------------------------
PEOPLE = [
    # name, team, role
    ("Ava Chen",        "Platform",     "VP Engineering"),
    ("Marcus Okoro",    "Platform",     "Staff Engineer"),
    ("Priya Shah",      "Data",         "Head of Data"),
    ("Diego Ramos",     "Data",         "Data Engineer"),
    ("Elena Petrova",   "Security",     "Head of Security"),
    ("Noah Williams",   "Product",      "Director of Product"),
    ("Sofia Marin",     "Product",      "Product Manager"),
    ("Kenji Tanaka",    "Sales",        "VP Sales"),
    ("Fatima Zahra",    "Support",      "Head of Support"),
    ("Liam O'Brien",    "Finance",      "Controller"),
    ("Grace Adeyemi",   "People Ops",   "Head of People"),
    ("Oskar Novak",     "Platform",     "Site Reliability Eng"),
]

# name, status, owner
PROJECTS = [
    ("Atlas Migration",     "At Risk",     "Marcus Okoro"),
    ("Billing Revamp",      "On Track",    "Sofia Marin"),
    ("SOC 2 Type II",       "At Risk",     "Elena Petrova"),
    ("Data Lakehouse",      "On Track",    "Priya Shah"),
    ("Self-Serve Onboard",  "On Track",    "Noah Williams"),
    ("EU Region Launch",    "Blocked",     "Ava Chen"),
    ("Support Copilot",     "On Track",    "Fatima Zahra"),
    ("Q3 Comp Planning",    "On Track",    "Grace Adeyemi"),
]

# title, project, assignee, status, priority
WORK = [
    ("Cut over auth service to Atlas",      "Atlas Migration",    "Marcus Okoro",  "Blocked",     "P0"),
    ("Backfill legacy user records",        "Atlas Migration",    "Diego Ramos",   "In Progress", "P1"),
    ("Decommission old billing DB",         "Atlas Migration",    "Oskar Novak",   "Blocked",     "P1"),
    ("Metered usage pricing engine",        "Billing Revamp",     "Sofia Marin",   "In Progress", "P1"),
    ("Evidence collection automation",      "SOC 2 Type II",      "Elena Petrova", "Blocked",     "P0"),
    ("Access review workflow",              "SOC 2 Type II",      "Oskar Novak",   "Todo",        "P1"),
    ("Streaming ingestion pipeline",        "Data Lakehouse",     "Diego Ramos",   "In Progress", "P2"),
    ("Frictionless signup flow",            "Self-Serve Onboard", "Noah Williams", "In Progress", "P1"),
    ("EU data residency controls",          "EU Region Launch",   "Ava Chen",      "Blocked",     "P0"),
    ("Deploy pipeline in eu-west-1",        "EU Region Launch",   "Oskar Novak",   "Todo",        "P1"),
    ("Ticket triage model v2",              "Support Copilot",    "Fatima Zahra",  "In Progress", "P2"),
    ("Merit matrix + bands",                "Q3 Comp Planning",   "Grace Adeyemi", "In Progress", "P2"),
]

# name, kind, description
SYSTEMS = [
    ("Salesforce",  "CRM",     "Pipeline, accounts, opportunities"),
    ("Workday",     "HR",      "People, org chart, comp"),
    ("NetSuite",    "Finance", "GL, billing, revenue"),
    ("Zendesk",     "Support", "Tickets, CSAT, SLAs"),
    ("Jira",        "Work",    "Projects, epics, work items"),
    ("Okta",        "Identity","SSO, access, provisioning"),
]


def seed(conn):
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE people   (id INTEGER PRIMARY KEY, name TEXT, team TEXT, role TEXT);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, status TEXT, owner TEXT);
        CREATE TABLE work     (id INTEGER PRIMARY KEY, title TEXT, project TEXT,
                               assignee TEXT, status TEXT, priority TEXT);
        CREATE TABLE systems  (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, descr TEXT);
        -- ambient action layer: persisted decisions on drafted alerts
        CREATE TABLE alert_actions (
            alert_id  TEXT PRIMARY KEY,
            decision  TEXT,             -- 'approved' | 'dismissed'
            effect    TEXT,             -- human-readable applied change
            ts        TEXT
        );
        """
    )
    c.executemany("INSERT INTO people(name,team,role) VALUES(?,?,?)", PEOPLE)
    c.executemany("INSERT INTO projects(name,status,owner) VALUES(?,?,?)", PROJECTS)
    c.executemany("INSERT INTO work(title,project,assignee,status,priority) VALUES(?,?,?,?,?)", WORK)
    c.executemany("INSERT INTO systems(name,kind,descr) VALUES(?,?,?)", SYSTEMS)
    conn.commit()


def ensure_schema(conn):
    """Additive migration so an already-seeded db from an earlier build still works."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS alert_actions (
               alert_id TEXT PRIMARY KEY, decision TEXT, effect TEXT, ts TEXT)"""
    )
    # autopilot activity log — additive, independent of alert_actions
    conn.execute(
        """CREATE TABLE IF NOT EXISTS autopilot_log (
               id        INTEGER PRIMARY KEY AUTOINCREMENT,
               ts        TEXT,
               alert_id  TEXT,
               alert     TEXT,      -- the alert headline the cycle acted on
               decision  TEXT,      -- 'applied' | 'queued' | 'skipped'
               rationale TEXT,
               applied   INTEGER    -- 1 if the action was auto-applied, else 0
           )"""
    )
    conn.commit()


def get_conn():
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if fresh:
        seed(conn)
    else:
        ensure_schema(conn)
    return conn


def rows_as_dicts(cur):
    return [dict(r) for r in cur.fetchall()]


def snapshot(conn):
    """Full operational graph as plain dicts (for the UI + Claude context)."""
    c = conn.cursor()
    return {
        "company": "Northwind Labs",
        "people":   rows_as_dicts(c.execute("SELECT * FROM people")),
        "projects": rows_as_dicts(c.execute("SELECT * FROM projects")),
        "work":     rows_as_dicts(c.execute("SELECT * FROM work")),
        "systems":  rows_as_dicts(c.execute("SELECT * FROM systems")),
    }


# --------------------------------------------------------------------------
# "Ask the company" — grounded retrieval + answer
# --------------------------------------------------------------------------
STATUS_BAD = {"At Risk", "Blocked"}


def retrieve(conn, question):
    """Naive but explainable retrieval: score rows by keyword overlap with the
    question, always include at-risk/blocked signal. Returns the rows we drew
    from so the UI can cite sources."""
    q = question.lower()
    words = set(re.findall(r"[a-z0-9]+", q))
    snap = snapshot(conn)

    def score(text):
        t = text.lower()
        toks = set(re.findall(r"[a-z0-9]+", t))
        overlap = len(words & toks)
        # phrase bonus
        if any(w in q for w in re.findall(r"[a-z]{4,}", t)):
            overlap += 1
        return overlap

    risky_words = {"risk", "risks", "blocked", "block", "blocker", "blockers",
                   "slip", "slipped", "late", "danger", "week", "attention"}
    wants_risk = bool(words & risky_words)

    people = sorted(snap["people"],
                    key=lambda r: score(f"{r['name']} {r['team']} {r['role']}"),
                    reverse=True)
    projects = sorted(
        snap["projects"],
        key=lambda r: (score(f"{r['name']} {r['owner']} {r['status']}")
                       + (2 if wants_risk and r["status"] in STATUS_BAD else 0)),
        reverse=True)
    work = sorted(
        snap["work"],
        key=lambda r: (score(f"{r['title']} {r['project']} {r['assignee']} {r['status']} {r['priority']}")
                       + (2 if wants_risk and r["status"] == "Blocked" else 0)),
        reverse=True)
    systems = sorted(snap["systems"],
                     key=lambda r: score(f"{r['name']} {r['kind']} {r['descr']}"),
                     reverse=True)

    top_people = [p for p in people if score(f"{p['name']} {p['team']} {p['role']}") > 0][:4] or people[:3]
    top_projects = projects[:4]
    top_work = work[:5]
    top_systems = [s for s in systems if score(f"{s['name']} {s['kind']} {s['descr']}") > 0][:3]

    return {
        "people":   top_people,
        "projects": top_projects,
        "work":     top_work,
        "systems":  top_systems,
    }


def build_context(ctx):
    lines = ["OPERATIONAL GRAPH (grounded facts about Northwind Labs):", ""]
    lines.append("PEOPLE:")
    for p in ctx["people"]:
        lines.append(f"  - {p['name']} — {p['role']}, {p['team']} team")
    lines.append("PROJECTS (name — status — owner):")
    for p in ctx["projects"]:
        lines.append(f"  - {p['name']} — {p['status']} — owner {p['owner']}")
    lines.append("WORK ITEMS (title — project — assignee — status — priority):")
    for w in ctx["work"]:
        lines.append(f"  - {w['title']} — {w['project']} — {w['assignee']} — {w['status']} — {w['priority']}")
    lines.append("SYSTEMS:")
    for s in ctx["systems"]:
        lines.append(f"  - {s['name']} ({s['kind']}): {s['descr']}")
    return "\n".join(lines)


def answer_question(conn, question):
    ctx = retrieve(conn, question)
    context = build_context(ctx)
    sources = {
        "people":   [p["name"] for p in ctx["people"]],
        "projects": [p["name"] for p in ctx["projects"]],
        "systems":  [s["name"] for s in ctx["systems"]],
    }
    if API_KEY:
        prompt = (
            "You are Mesa, the ambient operational brain for a mid-market company. "
            "Answer the employee's question using ONLY the operational graph below. "
            "Be concise (2-4 sentences), specific, name people and projects, and if "
            "something is at risk or blocked, say so plainly and suggest the next action. "
            "Do not invent facts not present in the graph.\n\n"
            f"{context}\n\n"
            f"QUESTION: {question}\n\nANSWER:"
        )
        try:
            text = claude_call(prompt)
            if text:
                return {"answer": text, "sources": sources, "engine": "claude"}
        except Exception:
            pass  # fall through to mock
    return {"answer": mock_answer(conn, question, ctx), "sources": sources, "engine": "mock"}


def mock_answer(conn, question, ctx):
    q = question.lower()
    snap = snapshot(conn)
    at_risk = [p for p in snap["projects"] if p["status"] in STATUS_BAD]
    blocked = [w for w in snap["work"] if w["status"] == "Blocked"]

    if any(k in q for k in ("risk", "at risk", "attention", "this week", "slip")):
        names = ", ".join(f"{p['name']} ({p['status']}, owner {p['owner']})" for p in at_risk)
        return (f"{len(at_risk)} projects need attention this week: {names}. "
                f"There are {len(blocked)} blocked work items across them — the sharpest is "
                f"'{blocked[0]['title']}' ({blocked[0]['priority']}, {blocked[0]['assignee']}). "
                "Recommend a blocker-clearing sync with the named owners today.")
    if "block" in q:
        lst = "; ".join(f"{w['title']} — {w['assignee']} ({w['project']})" for w in blocked[:4])
        return (f"We're blocked in {len(blocked)} places: {lst}. "
                "Most trace back to Atlas Migration and EU Region Launch dependencies. "
                "Next action: escalate the P0s to Ava Chen and Marcus Okoro.")
    if any(k in q for k in ("who owns", "owner", "who is responsible", "who leads")):
        p = ctx["projects"][0]
        return (f"{p['owner']} owns '{p['name']}' (currently {p['status']}). "
                f"Related work is being carried by "
                f"{', '.join(sorted({w['assignee'] for w in ctx['work']}))}.")
    if any(k in q for k in ("who should i ask", "who knows", "expert", "who can help")):
        ppl = ", ".join(f"{p['name']} ({p['role']})" for p in ctx["people"][:3])
        return (f"Best people to ask: {ppl}. "
                "They own or are closest to the projects that match your question.")
    # generic
    proj = ctx["projects"][0]
    ppl = ", ".join(p["name"] for p in ctx["people"][:3])
    return (f"Most relevant right now: '{proj['name']}' ({proj['status']}, owner {proj['owner']}). "
            f"People closest to this: {ppl}. "
            f"The company is tracking {len(snap['projects'])} projects, "
            f"{len(at_risk)} of them needing attention.")


def ambient_alert(conn):
    """The proactive brain: surface the single most urgent operational signal
    and draft the action — no one asked; it just knows."""
    snap = snapshot(conn)
    blocked = [w for w in snap["work"] if w["status"] == "Blocked"]
    # find the project with the most blockers
    by_proj = {}
    for w in blocked:
        by_proj.setdefault(w["project"], []).append(w)
    if not by_proj:
        return None
    proj_name, items = max(by_proj.items(), key=lambda kv: len(kv[1]))
    proj = next((p for p in snap["projects"] if p["name"] == proj_name), None)
    owner = proj["owner"] if proj else items[0]["assignee"]
    # owner overload = how many blocked/in-progress items they carry
    load = [w for w in snap["work"]
            if w["assignee"] == owner and w["status"] in ("Blocked", "In Progress")]
    p0 = [w for w in items if w["priority"] == "P0"]

    headline = (f"{proj_name} slipped — {len(items)} blocker"
                f"{'s' if len(items) != 1 else ''}, owner {owner} overloaded "
                f"({len(load)} active items).")
    detail = "; ".join(f"{w['title']} ({w['priority']}, {w['assignee']})" for w in items)
    draft = (
        f"Suggested action: schedule a 20-min blocker triage for '{proj_name}' with "
        f"{owner} and the assignees above today. "
        + (f"Escalate the {len(p0)} P0 item{'s' if len(p0) != 1 else ''} to leadership. "
           if p0 else "")
        + f"Consider re-balancing {owner}'s load across the team."
    )
    return {
        "headline": headline,
        "detail": detail,
        "draft": draft,
        "project": proj_name,
        "owner": owner,
        "blockers": len(items),
    }


# --------------------------------------------------------------------------
# Ambient action layer — a prioritized stream of proactive alerts, each with
# a DRAFTED action that Approve can actually apply to the graph.
# --------------------------------------------------------------------------
import datetime

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def compute_alerts(conn):
    """Derive proactive operational alerts from the seeded graph.

    Each alert has a STABLE id (so a persisted Approve/Dismiss survives
    recomputation), a severity, the cited people/projects, and a concrete
    drafted action carrying the state-change to apply on Approve."""
    snap = snapshot(conn)
    people = snap["people"]
    projects = snap["projects"]
    work = snap["work"]
    by_name = {p["name"]: p for p in people}
    alerts = []

    # active load per person (blocked / in-progress items they carry)
    load = {}
    for w in work:
        if w["status"] in ("Blocked", "In Progress"):
            load[w["assignee"]] = load.get(w["assignee"], 0) + 1

    # 1) Per at-risk/blocked project: slipping with blockers + overloaded owner
    blocked_by_proj = {}
    for w in work:
        if w["status"] == "Blocked":
            blocked_by_proj.setdefault(w["project"], []).append(w)

    for proj in projects:
        pname = proj["name"]
        blk = blocked_by_proj.get(pname, [])
        if proj["status"] not in STATUS_BAD and not blk:
            continue
        owner = proj["owner"]
        p0 = [w for w in blk if w["priority"] == "P0"]
        owner_load = load.get(owner, 0)
        sev = "critical" if (p0 or proj["status"] == "Blocked") else "high"

        cited_people = sorted({owner} | {w["assignee"] for w in blk})
        # pick the least-loaded teammate on the same team as a reassignment target
        team = by_name.get(owner, {}).get("team")
        mates = [p["name"] for p in people
                 if p.get("team") == team and p["name"] != owner]
        target = min(mates, key=lambda n: load.get(n, 0)) if mates else None

        headline = (
            f"{pname} slipped — {len(blk)} blocker{'s' if len(blk) != 1 else ''}"
            + (f", owner {owner} overloaded ({owner_load} active items)"
               if owner_load >= 3 else f", owner {owner}")
            + "."
        )
        detail = ("; ".join(f"{w['title']} ({w['priority']}, {w['assignee']})"
                            for w in blk) or f"Project status is {proj['status']}.")
        if owner_load >= 3 and target:
            draft = {
                "kind": "reassign_owner",
                "label": (f"Re-balance {owner}'s load: reassign '{pname}' to "
                          f"{target} and open a blocker-triage work item."),
                "project": pname,
                "from": owner,
                "to": target,
            }
        else:
            draft = {
                "kind": "flag_project",
                "label": (f"Flag '{pname}' for a 20-min blocker triage today with "
                          f"{owner} and add a tracking work item."),
                "project": pname,
                "assignee": owner,
            }
        alerts.append({
            "id": f"proj:{pname}",
            "severity": sev,
            "headline": headline,
            "detail": detail,
            "people": cited_people,
            "projects": [pname],
            "draft": draft,
        })

    # 2) Projects with no owner in the people graph (orphaned)
    orphans = [p for p in projects if p["owner"] not in by_name]
    if orphans:
        names = [p["name"] for p in orphans]
        # least-loaded VP/Head/Director to assign as interim owner
        leaders = [p for p in people
                   if any(k in p["role"] for k in ("VP", "Head", "Director"))]
        interim = (min(leaders, key=lambda p: load.get(p["name"], 0))["name"]
                   if leaders else people[0]["name"])
        alerts.append({
            "id": "orphans",
            "severity": "high" if len(orphans) >= 3 else "medium",
            "headline": (f"{len(orphans)} project{'s' if len(orphans) != 1 else ''} "
                         f"ha{'ve' if len(orphans) != 1 else 's'} no owner in the graph."),
            "detail": "Unowned: " + ", ".join(names) + ".",
            "people": [interim],
            "projects": names,
            "draft": {
                "kind": "assign_interim_owner",
                "label": f"Assign {interim} as interim owner across {len(orphans)} unowned project(s).",
                "projects": names,
                "to": interim,
            },
        })

    # 3) Support backlog rising — in-progress support work + support team load
    support_people = {p["name"] for p in people if p.get("team") == "Support"}
    support_open = [w for w in work
                    if w["project"] == "Support Copilot" or w["assignee"] in support_people]
    active_support = [w for w in support_open if w["status"] in ("Blocked", "In Progress")]
    if active_support:
        head = next((p["name"] for p in people
                     if p.get("team") == "Support" and "Head" in p["role"]), None)
        cited = sorted({w["assignee"] for w in active_support} | ({head} if head else set()))
        alerts.append({
            "id": "support-backlog",
            "severity": "medium",
            "headline": (f"Support backlog rising — {len(active_support)} active "
                         f"item{'s' if len(active_support) != 1 else ''} in flight."),
            "detail": "; ".join(f"{w['title']} ({w['status']})" for w in active_support) + ".",
            "people": cited,
            "projects": sorted({w["project"] for w in active_support}),
            "draft": {
                "kind": "create_workitem",
                "label": ("Spin up a 'Support surge triage' P1 work item on Support Copilot"
                          + (f" assigned to {head}." if head else ".")),
                "title": "Support surge triage",
                "project": "Support Copilot",
                "assignee": head or (sorted(support_people)[0] if support_people else "Unassigned"),
                "priority": "P1",
            },
        })

    # merge persisted decisions
    ensure_schema(conn)
    acted = {r["alert_id"]: dict(r)
             for r in conn.execute("SELECT * FROM alert_actions")}
    for a in alerts:
        rec = acted.get(a["id"])
        if rec:
            a["decision"] = rec["decision"]
            a["effect"] = rec["effect"]
            a["decided_at"] = rec["ts"]
        else:
            a["decision"] = None

    alerts.sort(key=lambda a: (a.get("decision") is not None,
                               SEV_RANK.get(a["severity"], 9)))
    return alerts


def get_alert(conn, alert_id):
    for a in compute_alerts(conn):
        if a["id"] == alert_id:
            return a
    return None


def apply_action(conn, alert_id, decision):
    """Approve => mutate the graph per the drafted action; Dismiss => just record.
    Idempotent: a previously-decided alert returns its recorded effect."""
    ensure_schema(conn)
    prior = conn.execute(
        "SELECT * FROM alert_actions WHERE alert_id=?", (alert_id,)).fetchone()
    if prior:
        return {"ok": True, "already": True, "decision": prior["decision"],
                "effect": prior["effect"]}

    alert = get_alert(conn, alert_id)
    if not alert:
        return {"ok": False, "error": "unknown alert_id"}

    decision = (decision or "").lower()
    if decision not in ("approve", "approved", "dismiss", "dismissed"):
        return {"ok": False, "error": "decision must be approve or dismiss"}
    approved = decision.startswith("approve")
    effect = "Dismissed — no change applied."

    if approved:
        d = alert["draft"]
        kind = d["kind"]
        c = conn.cursor()
        if kind == "reassign_owner":
            c.execute("UPDATE projects SET owner=? WHERE name=?", (d["to"], d["project"]))
            c.execute(
                "INSERT INTO work(title,project,assignee,status,priority) VALUES(?,?,?,?,?)",
                (f"Blocker triage: {d['project']}", d["project"], d["to"], "Todo", "P1"))
            effect = f"Reassigned '{d['project']}' owner {d['from']} → {d['to']} and opened a triage item."
        elif kind == "flag_project":
            c.execute("UPDATE projects SET status=? WHERE name=?", ("At Risk", d["project"]))
            c.execute(
                "INSERT INTO work(title,project,assignee,status,priority) VALUES(?,?,?,?,?)",
                (f"Blocker triage: {d['project']}", d["project"], d["assignee"], "Todo", "P1"))
            effect = f"Flagged '{d['project']}' At Risk and opened a blocker-triage item for {d['assignee']}."
        elif kind == "assign_interim_owner":
            for pn in d["projects"]:
                c.execute("UPDATE projects SET owner=? WHERE name=?", (d["to"], pn))
            effect = f"Assigned {d['to']} as interim owner across {len(d['projects'])} project(s)."
        elif kind == "create_workitem":
            c.execute(
                "INSERT INTO work(title,project,assignee,status,priority) VALUES(?,?,?,?,?)",
                (d["title"], d["project"], d["assignee"], "Todo", d["priority"]))
            effect = f"Created '{d['title']}' ({d['priority']}) on {d['project']} → {d['assignee']}."
        else:
            effect = "Approved — no automated change mapped."
        conn.commit()

    conn.execute(
        "INSERT INTO alert_actions(alert_id,decision,effect,ts) VALUES(?,?,?,?)",
        (alert_id, "approved" if approved else "dismissed", effect, _now()))
    conn.commit()

    return {
        "ok": True,
        "decision": "approved" if approved else "dismissed",
        "effect": effect,
        "alert_id": alert_id,
        "graph": snapshot(conn),
        "alerts": compute_alerts(conn),
    }


# --------------------------------------------------------------------------
# Autopilot — an autonomous perceive → reason → act loop.
#
#   perceive : read the current alerts
#   reason   : pick the highest-severity SAFE, still-open alert; ask Claude
#              (mock fallback) for a one-line rationale
#   act      : if the drafted action is low-risk, apply it via the existing
#              act logic; if high-risk, queue it for human approval
#
# Every cycle appends a row to autopilot_log. Guardrail: safe-actions-only.
# --------------------------------------------------------------------------

# low-risk action kinds Autopilot may auto-apply; anything else needs a human.
SAFE_KINDS = {"create_workitem", "flag_project", "assign_interim_owner"}
HIGH_RISK_KINDS = {"reassign_owner"}


def _action_is_safe(alert):
    draft = alert.get("draft") or {}
    return draft.get("kind") in SAFE_KINDS


def autopilot_rationale(alert, safe):
    """One-line rationale for the decision. Claude if available, mock otherwise."""
    draft = alert.get("draft") or {}
    label = draft.get("label", "")
    if API_KEY:
        prompt = (
            "You are Mesa Autopilot, an autonomous operations copilot. In ONE short "
            "sentence (max 30 words), explain why you are "
            + ("auto-applying" if safe else "holding for human approval")
            + " this operational action. Be concrete and calm.\n\n"
            f"ALERT ({alert.get('severity')}): {alert.get('headline')}\n"
            f"DETAIL: {alert.get('detail')}\n"
            f"DRAFTED ACTION: {label}\n\n"
            "RATIONALE:"
        )
        try:
            text = claude_call(prompt, max_tokens=120)
            if text:
                return text.split("\n")[0].strip()
        except Exception:
            pass
    # mock fallback
    if safe:
        return (f"Highest-severity safe signal — a low-risk {draft.get('kind','action')} "
                f"that only adds tracking without moving people, so applying automatically.")
    return (f"Highest-severity signal but this is a high-risk {draft.get('kind','action')} "
            f"that reassigns an overloaded owner — holding for human approval.")


def autopilot_step(conn):
    """Run exactly one perceive → reason → act cycle. Returns the cycle record."""
    ensure_schema(conn)
    # perceive
    alerts = compute_alerts(conn)
    open_alerts = [a for a in alerts if not a.get("decision")]

    if not open_alerts:
        rec = {"ts": _now(), "alert_id": None, "alert": "All clear",
               "decision": "skipped", "applied": False,
               "rationale": "No open alerts to act on — the graph is quiet.",
               "engine": "claude" if API_KEY else "mock"}
        conn.execute(
            "INSERT INTO autopilot_log(ts,alert_id,alert,decision,rationale,applied) "
            "VALUES(?,?,?,?,?,?)",
            (rec["ts"], None, rec["alert"], rec["decision"], rec["rationale"], 0))
        conn.commit()
        rec.update({"state": autopilot_state(conn), "graph": None, "alerts": alerts})
        return rec

    # reason: highest-severity open alert (list is already severity-sorted)
    target = min(open_alerts, key=lambda a: SEV_RANK.get(a["severity"], 9))
    safe = _action_is_safe(target)
    rationale = autopilot_rationale(target, safe)

    # act
    applied = False
    effect = ""
    graph = None
    if safe:
        res = apply_action(conn, target["id"], "approve")
        applied = bool(res.get("ok")) and not res.get("already")
        effect = res.get("effect", "")
        graph = res.get("graph")
        decision = "applied" if applied else "skipped"
        if not applied and res.get("already"):
            decision = "skipped"
            rationale = f"Already handled — {effect}"
    else:
        decision = "queued"   # high-risk: surfaced for human approval, no mutation

    conn.execute(
        "INSERT INTO autopilot_log(ts,alert_id,alert,decision,rationale,applied) "
        "VALUES(?,?,?,?,?,?)",
        (_now(), target["id"], target["headline"], decision, rationale, 1 if applied else 0))
    conn.commit()

    return {
        "ts": _now(),
        "alert_id": target["id"],
        "alert": target["headline"],
        "severity": target["severity"],
        "decision": decision,
        "rationale": rationale,
        "applied": applied,
        "effect": effect,
        "safe": safe,
        "engine": "claude" if API_KEY else "mock",
        "graph": graph if applied else None,
        "alerts": compute_alerts(conn),
        "state": autopilot_state(conn),
    }


def autopilot_log(conn, limit=40):
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT ts,alert_id,alert,decision,rationale,applied FROM autopilot_log "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["applied"] = bool(d["applied"])
        out.append(d)
    return out


def autopilot_state(conn):
    """{running, applied_count, pending_approvals}. `running` is client-driven;
    the server reports it as False (the toggle lives in the browser) but exposes
    the counts the UI needs."""
    ensure_schema(conn)
    applied_count = conn.execute(
        "SELECT COUNT(*) FROM autopilot_log WHERE applied=1").fetchone()[0]
    # pending approvals = open high-risk alerts awaiting a human
    pending = 0
    for a in compute_alerts(conn):
        if not a.get("decision") and (a.get("draft") or {}).get("kind") in HIGH_RISK_KINDS:
            pending += 1
    return {
        "running": False,
        "applied_count": applied_count,
        "pending_approvals": pending,
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
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
        if self.path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, {"error": "index.html missing"})
            return
        if self.path == "/api/graph":
            conn = get_conn()
            try:
                self._send(200, snapshot(conn))
            finally:
                conn.close()
            return
        if self.path == "/api/ambient":
            conn = get_conn()
            try:
                self._send(200, {"alert": ambient_alert(conn)})
            finally:
                conn.close()
            return
        if self.path == "/api/alerts":
            conn = get_conn()
            try:
                self._send(200, {"alerts": compute_alerts(conn)})
            finally:
                conn.close()
            return
        if self.path == "/api/autopilot/log":
            conn = get_conn()
            try:
                self._send(200, {"log": autopilot_log(conn)})
            finally:
                conn.close()
            return
        if self.path == "/api/autopilot/state":
            conn = get_conn()
            try:
                self._send(200, autopilot_state(conn))
            finally:
                conn.close()
            return
        if self.path == "/api/health":
            self._send(200, {"ok": True, "engine": "claude" if API_KEY else "mock", "port": PORT})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/api/ask", "/api/act", "/api/autopilot/step"):
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return

        if self.path == "/api/autopilot/step":
            conn = get_conn()
            try:
                self._send(200, autopilot_step(conn))
            finally:
                conn.close()
            return

        if self.path == "/api/act":
            alert_id = (payload.get("alert_id") or "").strip()
            decision = (payload.get("decision") or "").strip()
            if not alert_id or not decision:
                self._send(400, {"error": "alert_id and decision required"})
                return
            conn = get_conn()
            try:
                res = apply_action(conn, alert_id, decision)
            finally:
                conn.close()
            self._send(200 if res.get("ok") else 400, res)
            return

        question = (payload.get("question") or "").strip()
        if not question:
            self._send(400, {"error": "empty question"})
            return
        conn = get_conn()
        try:
            self._send(200, answer_question(conn, question))
        finally:
            conn.close()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    eng = "Claude (claude-sonnet-4-6)" if API_KEY else "MOCK (no API key)"
    print(f"Mesa — operational brain — http://localhost:{PORT}  [engine: {eng}]")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
