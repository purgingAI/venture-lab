#!/usr/bin/env python3
"""
Command Center — a live operations command center that unifies all 10 Venture Lab apps.

Python 3 stdlib only (http.server + urllib + json). No installs, no browsers.

Endpoints:
  GET /              -> single-file index.html (futuristic ACE-family command center)
  GET /api/fleet     -> server-side polls all 10 apps concurrently (1s timeout each),
                        returns their live status + one headline metric each.
                        Apps that are down are marked offline (○) — never crashes.
  GET /api/agents    -> fleet-wide autonomous-operations feed. Concurrently polls
                        every app that exposes /api/autopilot/log + /api/autopilot/state
                        (1s timeout, graceful skip on down/404), merges all agent
                        actions into ONE newest-first timeline tagged by app+color,
                        and totals autonomous actions + running agents. Never crashes.
  GET /api/health    -> {"ok": true, ...}

Run:  ./run.sh            (foreground)
      ./run.sh -d         (detached)
      ./run.sh stop|status
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus

PORT = 8811
HOST = "127.0.0.1"
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "index.html")
POLL_TIMEOUT = 1.0  # seconds per app — keep the dashboard snappy even when apps are down


# --------------------------------------------------------------------------- #
# Fleet definition — the 10 Venture Lab apps. Each entry knows how to pull
# ONE headline metric out of that app's real endpoint (verified against each
# app's server.py). `probe(data)` returns a short human string; if the endpoint
# is unreachable the app is simply marked offline.
# --------------------------------------------------------------------------- #
def _money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) >= 1000:
        return f"${n/1000:.1f}k"
    return f"${n:,.0f}"


def _tradepilot(d):
    t = d.get("totals", {})
    return "collected", _money(t.get("collected", 0)), f"pipeline {len(d.get('jobs', []))} jobs"


def _emberhealth(d):
    fc = d.get("forecast", {})
    return "flare risk", f"{fc.get('risk', '–')}/100", str(fc.get("band", "")).title()


def _claimguard(d):
    s = d.get("stats", {})
    return "revenue protected", _money(s.get("protected", 0)), f"{_money(s.get('exposure', 0))} at-risk"


def _freightnormal(d):
    lanes = d.get("lanes", [])
    return "lanes tracked", str(len(lanes)), (lanes[0]["lane"] if lanes else "no lanes")


def _closeloop(d):
    return ("clean close",
            f"{d.get('cleared', 0)}/{d.get('total_transactions', 0)}",
            f"{_money(d.get('total_out_of_balance', 0))} out-of-balance")


def _ledgerbridge(d):
    intel = d.get("intel", {})
    return "mapped", f"{intel.get('mapped_pct', 0)}%", f"{_money(intel.get('open_drift', 0))} drift"


def _lorekeeper(d):
    return "net payout", _money(d.get("net", 0)), f"{d.get('order_count', 0)} orders"


def _compliagent(d):
    return "compliance score", f"{d.get('score', '–')}/100", str(d.get("ai_mode", "")).split()[0]


def _focusforge(d):
    return ("current streak",
            f"{d.get('currentStreak', 0)}🔥",
            f"{d.get('totalMinutes', 0)} min focused")


def _mesa(d):
    alerts = d.get("alerts", [])
    high = sum(1 for a in alerts if str(a.get("severity", "")).lower() in ("high", "critical"))
    return "open alerts", str(len(alerts)), f"{high} high-severity"


FLEET = [
    {"slot": 1,  "port": 8801, "name": "TradePilot",   "tag": "AI ops for trade businesses",
     "color": "#22d3ee", "path": "/api/state",     "probe": _tradepilot},
    {"slot": 2,  "port": 8802, "name": "EmberHealth",  "tag": "Flare-prediction OS",
     "color": "#fb7185", "path": "/api/state",     "probe": _emberhealth},
    {"slot": 3,  "port": 8803, "name": "ClaimGuard",   "tag": "Claim-denial radar",
     "color": "#34d399", "path": "/api/dashboard", "probe": _claimguard},
    {"slot": 4,  "port": 8804, "name": "FreightNormal","tag": "Freight rate normalizer",
     "color": "#f59e0b", "path": "/api/lanes",     "probe": _freightnormal},
    {"slot": 5,  "port": 8805, "name": "CloseLoop",    "tag": "Reconciliation autopilot",
     "color": "#60a5fa", "path": "/api/portfolio",  "probe": _closeloop},
    {"slot": 6,  "port": 8806, "name": "LedgerBridge", "tag": "Cross-ledger drift monitor",
     "color": "#a78bfa", "path": "/api/state",     "probe": _ledgerbridge},
    {"slot": 7,  "port": 8807, "name": "LoreKeeper",   "tag": "Creator revenue engine",
     "color": "#e879f9", "path": "/api/revenue",    "probe": _lorekeeper},
    {"slot": 8,  "port": 8808, "name": "CompliAgent",  "tag": "Compliance radar",
     "color": "#2dd4bf", "path": "/api/bootstrap",  "probe": _compliagent},
    {"slot": 9,  "port": 8809, "name": "FocusForge",   "tag": "Focus / momentum OS",
     "color": "#facc15", "path": "/api/stats",     "probe": _focusforge},
    {"slot": 10, "port": 8810, "name": "Mesa",         "tag": "Autonomous ops copilot",
     "color": "#f472b6", "path": "/api/alerts",    "probe": _mesa},
]


def poll_app(app):
    """Poll ONE app. Never raises — returns a tile dict (online or offline)."""
    tile = {
        "slot": app["slot"],
        "port": app["port"],
        "name": app["name"],
        "tag": app["tag"],
        "color": app["color"],
        "url": f"http://localhost:{app['port']}/",
        "online": False,
        "dot": "○",
        "metric_label": "",
        "metric": "offline",
        "sub": "unreachable",
    }
    url = f"http://{HOST}:{app['port']}{app['path']}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CommandCenter/1.0"})
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", "replace"))
        label, metric, sub = app["probe"](data)
        tile.update({
            "online": True, "dot": "●",
            "metric_label": label, "metric": metric, "sub": sub,
        })
    except Exception as exc:  # noqa: BLE001 — any failure => offline, never crash
        tile["sub"] = type(exc).__name__
    return tile


def build_fleet():
    """Poll all 10 apps concurrently and return the aggregate payload."""
    with ThreadPoolExecutor(max_workers=len(FLEET)) as ex:
        tiles = list(ex.map(poll_app, FLEET))
    tiles.sort(key=lambda t: t["slot"])
    online = sum(1 for t in tiles if t["online"])
    # aggregate "revenue signals" — count of tiles reporting a live headline metric
    signals = sum(1 for t in tiles if t["online"] and t["metric"] not in ("", "offline"))
    return {
        "tiles": tiles,
        "summary": {
            "total": len(tiles),
            "online": online,
            "offline": len(tiles) - online,
            "revenue_signals": signals,
        },
    }


# --------------------------------------------------------------------------- #
# Autonomous Operations — fleet-wide agent activity aggregator.
#
# A handful of apps expose an autopilot agent at:
#     GET /api/autopilot/log    -> {"log": [ {...}, ... ]}   (newest-first)
#     GET /api/autopilot/state  -> {...counts, maybe "running"...}
#
# Field names differ per app, so we normalize defensively. Any app that is down,
# lacks the endpoint (404), times out, or returns junk is simply skipped — this
# aggregator must NEVER crash even with every app offline.
# --------------------------------------------------------------------------- #
AGENT_APPS = [
    {"port": 8810, "name": "Mesa",        "color": "#f472b6"},
    {"port": 8807, "name": "LoreKeeper",  "color": "#e879f9"},
    {"port": 8801, "name": "TradePilot",  "color": "#22d3ee"},
    {"port": 8808, "name": "CompliAgent", "color": "#2dd4bf"},
    {"port": 8803, "name": "ClaimGuard",  "color": "#2dd4bf"},
    {"port": 8804, "name": "FreightNormal","color": "#22d3ee"},
    {"port": 8806, "name": "LedgerBridge", "color": "#10b981"},
]

# candidate keys, in priority order, used to pull ONE human-readable string /
# timestamp / count out of arbitrary autopilot payloads.
_ACTION_KEYS = ("message", "decision", "action", "summary", "text",
                "rationale", "detail", "event", "note", "title", "alert")
_TS_KEYS = ("ts", "time", "timestamp", "at", "created_at", "date", "when")
_ACTION_COUNT_KEYS = ("applied_count", "actions", "action_count", "contacted_count",
                      "count", "total", "steps", "executed", "applied")


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return None


def _fetch_json(port, path):
    """GET one JSON endpoint. Returns dict/list on success, None on any failure."""
    url = f"http://{HOST}:{port}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CommandCenter/1.0"})
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — down / 404 / timeout / junk => skip
        return None


def _normalize_entry(app, raw):
    """Turn one arbitrary log row into a uniform timeline entry, or None."""
    if not isinstance(raw, dict):
        # allow bare strings too
        if isinstance(raw, str) and raw.strip():
            return {"app": app["name"], "color": app["color"],
                    "action": raw.strip(), "ts": ""}
        return None
    action = _first_str(raw, _ACTION_KEYS)
    # enrich: many rows have a subject we can prefix (fan / alert), keep it short
    subject = None
    for k in ("fan", "alert", "subject", "target", "name"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            subject = v.strip()
            break
    if action and subject and subject.lower() not in action.lower():
        action = f"{subject}: {action}"
    elif not action and subject:
        action = subject
    if not action:
        return None
    return {
        "app": app["name"],
        "color": app["color"],
        "action": action,
        "ts": _first_str(raw, _TS_KEYS) or "",
    }


def poll_agent(app):
    """Poll ONE app's autopilot endpoints. Never raises. Returns a dict with the
    app's normalized entries + a coarse (running, actions) read, or offline flag."""
    result = {
        "name": app["name"], "port": app["port"], "color": app["color"],
        "online": False, "running": False, "actions": 0, "entries": [],
    }
    log_data = _fetch_json(app["port"], "/api/autopilot/log")
    state_data = _fetch_json(app["port"], "/api/autopilot/state")
    if log_data is None and state_data is None:
        return result  # down or no autopilot — skip

    result["online"] = True

    # ----- entries from the log -----
    rows = []
    if isinstance(log_data, dict):
        for key in ("log", "entries", "items", "events", "activity"):
            if isinstance(log_data.get(key), list):
                rows = log_data[key]
                break
        else:
            # a dict without an obvious list wrapper — nothing usable
            rows = []
    elif isinstance(log_data, list):
        rows = log_data

    for raw in rows:
        entry = _normalize_entry(app, raw)
        if entry:
            result["entries"].append(entry)

    # ----- coarse totals from state (fall back to log length) -----
    running = False
    actions = 0
    if isinstance(state_data, dict):
        running = bool(state_data.get("running", False))
        cnt = _first_str(state_data, _ACTION_COUNT_KEYS)
        if cnt is not None:
            try:
                actions = int(float(cnt))
            except (TypeError, ValueError):
                actions = 0
    if actions <= 0:
        actions = len(result["entries"])
    result["running"] = running
    result["actions"] = actions
    return result


def build_agents(cap=40):
    """Concurrently aggregate every autopilot app into ONE merged timeline."""
    with ThreadPoolExecutor(max_workers=len(AGENT_APPS)) as ex:
        polled = list(ex.map(poll_agent, AGENT_APPS))

    timeline, agents, total_actions, running = [], [], 0, 0
    for p in polled:
        agents.append({
            "name": p["name"], "port": p["port"], "color": p["color"],
            "online": p["online"], "running": p["running"], "actions": p["actions"],
        })
        if p["online"]:
            total_actions += p["actions"]
            if p["running"]:
                running += 1
            timeline.extend(p["entries"])

    # newest first: log rows already arrive newest-first per app; sort by ts when
    # it's a comparable string, else preserve interleaved order via stable sort.
    def _sortkey(e):
        return e.get("ts") or ""
    if any(e.get("ts") for e in timeline):
        timeline.sort(key=_sortkey, reverse=True)
    timeline = timeline[:cap]

    active_agents = sum(1 for a in agents if a["online"])
    return {
        "timeline": timeline,
        "agents": agents,
        "summary": {
            "total_actions": total_actions,
            "running": running,
            "active_agents": active_agents,
            "agent_count": len(agents),
        },
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "CommandCenter/1.0"

    def log_message(self, fmt, *args):  # quiet logs
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path.startswith("/index"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "index.html missing"})
            return
        if path == "/api/health":
            self._send(HTTPStatus.OK, {"ok": True, "app": "command-center",
                                       "port": PORT, "fleet_size": len(FLEET)})
            return
        if path == "/api/fleet":
            try:
                self._send(HTTPStatus.OK, build_fleet())
            except Exception as exc:  # noqa: BLE001 — endpoint must never 500
                self._send(HTTPStatus.OK, {"tiles": [], "summary": {},
                                           "error": str(exc)})
            return
        if path == "/api/agents":
            try:
                self._send(HTTPStatus.OK, build_agents())
            except Exception as exc:  # noqa: BLE001 — endpoint must never 500
                self._send(HTTPStatus.OK, {"timeline": [], "agents": [],
                                           "summary": {}, "error": str(exc)})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Command Center → http://localhost:{PORT}  (unifying {len(FLEET)} apps)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
