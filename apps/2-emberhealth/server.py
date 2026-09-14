#!/usr/bin/env python3
"""
EmberHealth — a flare-prediction OS for ignored chronic conditions
(PCOS, endometriosis, fibromyalgia).

Python 3 stdlib only. No external deps. Serves a single-file index.html
and a small JSON API on PORT 8802. Seeds a mock SQLite database on first
run (~30 days of symptom logs). Transparent, explainable flare-risk model.
Optional real Claude call if creator-os/.env has ANTHROPIC_API_KEY; MOCK
fallback otherwise.

NOT MEDICAL ADVICE — demo/illustrative only.
"""

import os
import re
import json
import math
import sqlite3
import random
import datetime as dt
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "emberhealth.db")
INDEX_PATH = os.path.join(HERE, "index.html")
PORT = 8802

# ----------------------------------------------------------------------------
# Domain model
# ----------------------------------------------------------------------------
CONDITIONS = {
    "pcos": {
        "label": "PCOS",
        # triggers that most drive this condition's flares, with base weights
        "triggers": {
            "dairy": 0.85, "sugar": 0.9, "stress": 0.8, "poor_sleep": 0.75,
            "gluten": 0.55, "weather": 0.35, "skipped_meals": 0.6,
        },
        "cycle_aware": True,
    },
    "endo": {
        "label": "Endometriosis",
        "triggers": {
            "gluten": 0.8, "stress": 0.75, "weather": 0.55, "dairy": 0.6,
            "poor_sleep": 0.7, "sugar": 0.5, "intense_exercise": 0.65,
        },
        "cycle_aware": True,
    },
    "fibro": {
        "label": "Fibromyalgia",
        "triggers": {
            "weather": 0.9, "poor_sleep": 0.95, "stress": 0.85,
            "overexertion": 0.8, "gluten": 0.45, "sugar": 0.5, "cold": 0.7,
        },
        "cycle_aware": False,
    },
}

ALL_TRIGGERS = sorted({t for c in CONDITIONS.values() for t in c["triggers"]})


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            condition TEXT NOT NULL,
            pain INTEGER NOT NULL,
            fatigue INTEGER NOT NULL,
            sleep INTEGER NOT NULL,
            stress INTEGER NOT NULL,
            cycle_day INTEGER,
            triggers TEXT NOT NULL DEFAULT '[]',
            note TEXT DEFAULT ''
        )
    """)
    conn.commit()
    # Seed only if empty
    n = conn.execute("SELECT COUNT(*) c FROM logs").fetchone()["c"]
    if n == 0:
        seed(conn)
    conn.close()
    return fresh


def seed(conn):
    """Generate ~30 days of realistic, correlated mock data for PCOS.

    We bake in a genuine latent pattern so the model + pattern-intelligence
    panel have something true to find: dairy/sugar/poor_sleep tend to
    precede pain spikes, and pain rises in the late luteal / menstrual phase.
    """
    random.seed(42)
    condition = "pcos"
    trig_pool = list(CONDITIONS[condition]["triggers"].keys())
    today = dt.date.today()
    start = today - dt.timedelta(days=29)
    cycle_len = 28
    # place "day 1" so that a menstrual window lands in the recent past
    cycle_offset = 6

    rows = []
    prev_triggers = []
    for i in range(30):
        d = start + dt.timedelta(days=i)
        cycle_day = ((i + cycle_offset) % cycle_len) + 1

        # baseline symptoms
        base_pain = 2.2
        base_fatigue = 3.0

        # cyclical contribution: rises pre-menstrual (day 24-28) and during (1-4)
        if cycle_day >= 24 or cycle_day <= 4:
            base_pain += 2.6
            base_fatigue += 1.8
        elif 11 <= cycle_day <= 15:  # ovulation twinge
            base_pain += 0.9

        # yesterday's triggers push today's pain up (lagged effect)
        lag = 0.0
        for t in prev_triggers:
            lag += CONDITIONS[condition]["triggers"].get(t, 0) * 1.1
        base_pain += min(lag, 3.2)
        base_fatigue += min(lag * 0.6, 2.0)

        pain = int(max(0, min(10, round(base_pain + random.uniform(-0.7, 0.7)))))
        fatigue = int(max(0, min(10, round(base_fatigue + random.uniform(-0.7, 0.7)))))
        # worse pain days tend to wreck sleep and raise stress
        sleep = int(max(0, min(10, round(7 - pain * 0.35 + random.uniform(-1, 1)))))
        stress = int(max(0, min(10, round(3 + pain * 0.3 + random.uniform(-1, 1)))))

        # today's triggers: correlated with stress/poor sleep, plus randomness
        today_triggers = []
        if random.random() < 0.42:
            today_triggers.append("dairy")
        if random.random() < 0.38:
            today_triggers.append("sugar")
        if sleep <= 4 and random.random() < 0.8:
            today_triggers.append("poor_sleep")
        if stress >= 6 and random.random() < 0.75:
            today_triggers.append("stress")
        if random.random() < 0.18:
            today_triggers.append(random.choice(trig_pool))
        today_triggers = sorted(set(today_triggers))

        rows.append((
            d.isoformat(), condition, pain, fatigue, sleep, stress,
            cycle_day, json.dumps(today_triggers), ""
        ))
        prev_triggers = today_triggers

    conn.executemany(
        "INSERT INTO logs (day, condition, pain, fatigue, sleep, stress, "
        "cycle_day, triggers, note) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Flare-risk model (transparent, explainable, no ML libs)
# ----------------------------------------------------------------------------
def _load_logs(conn, condition):
    rows = conn.execute(
        "SELECT * FROM logs WHERE condition=? ORDER BY day ASC", (condition,)
    ).fetchall()
    return [dict(r) for r in rows]


def compute_pattern_intelligence(logs, condition):
    """For each trigger, what fraction of flare days were preceded (same/prev
    day) by that trigger? A 'flare day' = pain >= 6. Returns sorted list.
    """
    trig_defs = CONDITIONS[condition]["triggers"]
    flare_idx = [i for i, r in enumerate(logs) if r["pain"] >= 6]
    total_flares = len(flare_idx)
    results = []
    for t in trig_defs:
        if total_flares == 0:
            results.append({"trigger": t, "precede_pct": 0, "flares": 0,
                            "weight": trig_defs[t]})
            continue
        hits = 0
        for i in flare_idx:
            same = t in json.loads(logs[i]["triggers"])
            prev = i > 0 and t in json.loads(logs[i - 1]["triggers"])
            if same or prev:
                hits += 1
        results.append({
            "trigger": t,
            "precede_pct": round(100 * hits / total_flares),
            "flares": total_flares,
            "weight": trig_defs[t],
        })
    results.sort(key=lambda x: (x["precede_pct"], x["weight"]), reverse=True)
    return results


def compute_correlations(logs, condition):
    """Deeper analytics per trigger.

    For each trigger we compute:
      * lift = P(flare | trigger present today or yesterday) vs the baseline
        P(flare) across all days, expressed as a % change ("% lift").
      * flare_rate_with / flare_rate_without for transparency.
      * n_with = how many days the trigger fired (support).

    We also compute a cycle-phase correlation (if the condition is cycle-aware
    and cycle_day is populated): flare rate in the pre-menstrual/menstrual
    window vs the follicular window, as a lift %.

    Everything is ranked by |lift| so the strongest signals surface first.
    """
    trig_defs = CONDITIONS[condition]["triggers"]
    n = len(logs)
    flare_flags = [1 if r["pain"] >= 6 else 0 for r in logs]
    total_flares = sum(flare_flags)
    baseline = (total_flares / n) if n else 0.0

    # pre-parse trigger sets per day (index i => set active today-or-yesterday)
    day_trigs = [set(json.loads(r["triggers"])) for r in logs]

    results = []
    for t in trig_defs:
        with_idx, without_idx = [], []
        for i in range(n):
            present = (t in day_trigs[i]) or (i > 0 and t in day_trigs[i - 1])
            (with_idx if present else without_idx).append(i)
        n_with = len(with_idx)
        n_without = len(without_idx)
        rate_with = (sum(flare_flags[i] for i in with_idx) / n_with) if n_with else 0.0
        rate_without = (sum(flare_flags[i] for i in without_idx) / n_without) if n_without else 0.0
        # lift vs baseline (guard divide-by-zero)
        lift = ((rate_with - baseline) / baseline * 100) if baseline > 0 else 0.0
        results.append({
            "trigger": t,
            "n_with": n_with,
            "flare_rate_with": round(100 * rate_with),
            "flare_rate_without": round(100 * rate_without),
            "baseline_pct": round(100 * baseline),
            "lift_pct": round(lift),
            "weight": trig_defs[t],
        })
    # rank by lift desc (strongest amplifiers first), then support
    results.sort(key=lambda x: (x["lift_pct"], x["n_with"]), reverse=True)

    # cycle-phase correlation
    cycle = None
    if CONDITIONS[condition]["cycle_aware"]:
        peri, follic = [], []  # perimenstrual (>=24 or <=4) vs follicular (5-13)
        for i, r in enumerate(logs):
            cd = r.get("cycle_day")
            if not cd:
                continue
            if cd >= 24 or cd <= 4:
                peri.append(flare_flags[i])
            elif 5 <= cd <= 13:
                follic.append(flare_flags[i])
        if peri and follic:
            rp = sum(peri) / len(peri)
            rf = sum(follic) / len(follic)
            clift = ((rp - rf) / rf * 100) if rf > 0 else (100.0 if rp > 0 else 0.0)
            cycle = {
                "peri_flare_pct": round(100 * rp),
                "follicular_flare_pct": round(100 * rf),
                "lift_pct": round(clift),
                "n_peri": len(peri),
                "n_follicular": len(follic),
            }

    return {
        "baseline_flare_pct": round(100 * baseline),
        "total_flares": total_flares,
        "days": n,
        "triggers": results,
        "cycle": cycle,
    }


def build_report_data(condition):
    """Assemble everything the clinician report needs into one JSON payload."""
    conn = connect()
    logs = _load_logs(conn, condition)
    conn.close()
    fc = forecast(logs, condition)
    patterns = compute_pattern_intelligence(logs, condition)
    corr = compute_correlations(logs, condition)
    narrative = ai_insight(condition, fc, patterns, kind="report")

    label = CONDITIONS[condition]["label"]
    days = [r["day"] for r in logs]
    date_range = {"start": days[0] if days else None,
                  "end": days[-1] if days else None,
                  "days": len(logs)}

    # summary stats
    def _avg(key):
        return round(sum(r[key] for r in logs) / len(logs), 1) if logs else 0
    def _peak(key):
        return max((r[key] for r in logs), default=0)
    flare_days = sum(1 for r in logs if r["pain"] >= 6)
    stats = {
        "avg_pain": _avg("pain"), "peak_pain": _peak("pain"),
        "avg_fatigue": _avg("fatigue"), "avg_sleep": _avg("sleep"),
        "avg_stress": _avg("stress"),
        "flare_days": flare_days,
        "flare_pct": round(100 * flare_days / len(logs)) if logs else 0,
    }

    # compact 30-day timeline table
    timeline = [{
        "day": r["day"], "pain": r["pain"], "fatigue": r["fatigue"],
        "sleep": r["sleep"], "stress": r["stress"],
        "cycle_day": r.get("cycle_day"),
        "flare": 1 if r["pain"] >= 6 else 0,
        "triggers": [pretty(t) for t in json.loads(r["triggers"])],
    } for r in logs[-30:]]

    # top correlated triggers (positive lift, has support)
    top_corr = [c for c in corr["triggers"] if c["lift_pct"] > 0 and c["n_with"] > 0][:6]

    return {
        "condition": condition,
        "label": label,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date_range": date_range,
        "forecast": {"risk": fc["risk"], "band": fc["band"], "drivers": fc["drivers"]},
        "stats": stats,
        "top_correlations": top_corr,
        "cycle": corr["cycle"],
        "baseline_flare_pct": corr["baseline_flare_pct"],
        "timeline": timeline,
        "narrative": narrative,
    }


def render_report_html(d):
    """Print-ready (Save-as-PDF) clinician report with a soft letterhead."""
    label = d["label"]
    dr = d["date_range"]
    fc = d["forecast"]
    st = d["stats"]
    band = fc["band"]
    band_col = {"high": "#e11d48", "elevated": "#d97706", "calm": "#0d9488"}.get(band, "#6d28d9")
    rng = (f'{dr["start"]} &rarr; {dr["end"]} · {dr["days"]} days'
           if dr["start"] else "no data")

    drivers_rows = "".join(
        f'<tr><td>{esc(x["label"])}</td><td class="num">+{x["points"]}</td>'
        f'<td class="det">{esc(x.get("detail",""))}</td></tr>'
        for x in fc["drivers"]) or '<tr><td colspan="3" class="det">Insufficient data</td></tr>'

    corr_rows = ""
    for c in d["top_correlations"]:
        sign = "+" if c["lift_pct"] >= 0 else ""
        corr_rows += (
            f'<tr><td>{esc(pretty(c["trigger"]))}</td>'
            f'<td class="num lift">{sign}{c["lift_pct"]}%</td>'
            f'<td class="num">{c["flare_rate_with"]}%</td>'
            f'<td class="num">{c["flare_rate_without"]}%</td>'
            f'<td class="num">{c["n_with"]}</td></tr>')
    if not corr_rows:
        corr_rows = '<tr><td colspan="5" class="det">No dominant trigger correlation yet</td></tr>'

    cyc = d.get("cycle")
    cycle_block = ""
    if cyc:
        cyc_sign = "+" if cyc["lift_pct"] >= 0 else ""
        cycle_block = (
            '<div class="callout">'
            f'<b>Cycle-phase correlation.</b> Flares occur on '
            f'<b>{cyc["peri_flare_pct"]}%</b> of peri-menstrual days '
            f'(day 24&ndash;28 / 1&ndash;4, n={cyc["n_peri"]}) vs '
            f'<b>{cyc["follicular_flare_pct"]}%</b> of follicular days '
            f'(day 5&ndash;13, n={cyc["n_follicular"]}) &mdash; a '
            f'<b style="color:{band_col}">{cyc_sign}{cyc["lift_pct"]}%</b> peri-menstrual lift.'
            '</div>')

    tl_rows = ""
    for r in d["timeline"]:
        fl = '<span class="flaredot"></span>' if r["flare"] else ''
        cd = r["cycle_day"] if r["cycle_day"] else "&mdash;"
        trg = ", ".join(esc(t) for t in r["triggers"]) or "&mdash;"
        tl_rows += (
            f'<tr><td>{esc(r["day"])} {fl}</td><td class="num">{r["pain"]}</td>'
            f'<td class="num">{r["fatigue"]}</td><td class="num">{r["sleep"]}</td>'
            f'<td class="num">{r["stress"]}</td><td class="num">{cd}</td>'
            f'<td class="det">{trg}</td></tr>')

    narrative = esc(d["narrative"]["text"])
    nsrc = "Claude (live)" if d["narrative"]["source"] == "claude" else "on-device summary engine"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>EmberHealth clinician report — {esc(label)}</title>
<style>
  @page {{ margin: 18mm 16mm; }}
  :root {{ --rose:#fb7185; --plum:#a78bfa; --ink:#2b2536; --muted:#7b7290; --line:#ece7f4; }}
  *{{box-sizing:border-box}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
    color:var(--ink);margin:0;background:#f7f5fb;line-height:1.5;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .sheet{{max-width:830px;margin:22px auto;background:#fff;padding:0 0 34px;
    box-shadow:0 10px 40px rgba(80,50,120,.12);border-radius:14px;overflow:hidden}}
  .letterhead{{background:linear-gradient(120deg,rgba(251,113,133,.16),rgba(167,139,250,.16));
    padding:26px 34px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px}}
  .mark{{width:46px;height:46px;border-radius:13px;flex:0 0 46px;
    background:radial-gradient(circle at 30% 30%,var(--rose),var(--plum));
    display:flex;align-items:center;justify-content:center;font-size:24px;
    box-shadow:0 6px 18px rgba(251,113,133,.35)}}
  .letterhead h1{{margin:0;font-size:21px;font-weight:700}}
  .letterhead h1 span{{background:linear-gradient(90deg,var(--rose),var(--plum));
    -webkit-background-clip:text;background-clip:text;color:transparent}}
  .letterhead .sub{{color:var(--muted);font-size:12.5px;margin-top:3px}}
  .meta{{margin-left:auto;text-align:right;font-size:12px;color:var(--muted)}}
  .meta b{{color:var(--ink)}}
  .body{{padding:8px 34px 0}}
  section{{margin-top:26px}}
  h2{{font-size:12.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--plum);
    font-weight:700;margin:0 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}}
  .riskcard{{display:flex;align-items:center;gap:20px;padding:16px 18px;border-radius:14px;
    background:linear-gradient(120deg,rgba(251,113,133,.07),rgba(167,139,250,.07));border:1px solid var(--line)}}
  .riskcard .big{{font-size:44px;font-weight:800;line-height:1;color:{band_col}}}
  .riskcard .big small{{font-size:13px;color:var(--muted);font-weight:600}}
  .pill{{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;
    letter-spacing:.6px;padding:4px 12px;border-radius:999px;color:{band_col};
    background:{band_col}1f;margin-top:6px}}
  .statgrid{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:4px}}
  .stat{{background:#faf8fe;border:1px solid var(--line);border-radius:11px;padding:11px 8px;text-align:center}}
  .stat b{{display:block;font-size:20px;color:var(--ink)}}
  .stat small{{font-size:9.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted)}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px}}
  th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
  th{{font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);font-weight:600}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  td.det{{color:var(--muted);font-size:11.5px}}
  td.lift{{color:{band_col};font-weight:700}}
  .callout{{margin-top:12px;font-size:12.5px;padding:12px 14px;border-radius:11px;
    background:#faf8fe;border:1px solid var(--line);border-left:3px solid var(--plum)}}
  .flaredot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:#f59e0b;margin-left:4px}}
  .narrative{{white-space:pre-wrap;font-size:12.8px;background:#faf8fe;border:1px solid var(--line);
    border-radius:12px;padding:15px 17px;line-height:1.6}}
  .nsrc{{font-size:10.5px;color:var(--muted);margin-top:7px}}
  .disc{{margin:30px 34px 0;padding-top:16px;border-top:1px solid var(--line);
    font-size:11px;color:var(--muted);line-height:1.55}}
  .disc b{{color:var(--rose)}}
  .toolbar{{max-width:830px;margin:22px auto -8px;text-align:right}}
  .printbtn{{background:linear-gradient(90deg,var(--rose),var(--plum));color:#1a0f1e;border:none;
    font-weight:700;padding:10px 18px;border-radius:11px;font-size:13px;cursor:pointer;
    box-shadow:0 6px 18px rgba(251,113,133,.3)}}
  @media print {{ body{{background:#fff}} .sheet{{box-shadow:none;margin:0;border-radius:0}} .toolbar{{display:none}} }}
</style></head>
<body>
  <div class="toolbar"><button class="printbtn" onclick="window.print()">Save as PDF / Print</button></div>
  <div class="sheet">
    <div class="letterhead">
      <div class="mark">🌸</div>
      <div>
        <h1>Ember<span>Health</span> · Clinician Report</h1>
        <div class="sub">Patient-generated flare-pattern summary for {esc(label)}</div>
      </div>
      <div class="meta">
        <div>Condition: <b>{esc(label)}</b></div>
        <div>Range: <b>{rng}</b></div>
        <div>Generated: <b>{esc(d["generated_at"].replace("T"," "))}</b></div>
      </div>
    </div>
    <div class="body">
      <section>
        <h2>Current flare-risk assessment</h2>
        <div class="riskcard">
          <div class="big">{fc["risk"]}<small>/100</small></div>
          <div>
            <div class="pill">{esc(band)} risk · next 3 days</div>
            <div style="font-size:12.5px;color:var(--muted);margin-top:7px">
              Model-estimated probability of a flare in the coming days, from a transparent
              weighted heuristic over the patient's recent logs.</div>
          </div>
        </div>
      </section>

      <section>
        <h2>Symptom summary ({dr["days"]} days)</h2>
        <div class="statgrid">
          <div class="stat"><b>{st["avg_pain"]}</b><small>Avg pain</small></div>
          <div class="stat"><b>{st["peak_pain"]}</b><small>Peak pain</small></div>
          <div class="stat"><b>{st["avg_fatigue"]}</b><small>Avg fatigue</small></div>
          <div class="stat"><b>{st["avg_sleep"]}</b><small>Avg sleep</small></div>
          <div class="stat"><b>{st["avg_stress"]}</b><small>Avg stress</small></div>
          <div class="stat"><b>{st["flare_days"]}</b><small>Flare days ({st["flare_pct"]}%)</small></div>
        </div>
      </section>

      <section>
        <h2>Current risk drivers</h2>
        <table><thead><tr><th>Driver</th><th class="num">Pts</th><th>Detail</th></tr></thead>
        <tbody>{drivers_rows}</tbody></table>
      </section>

      <section>
        <h2>Top correlated triggers (% lift vs {d["baseline_flare_pct"]}% baseline)</h2>
        <table><thead><tr><th>Trigger</th><th class="num">Lift</th>
          <th class="num">Flare% with</th><th class="num">without</th><th class="num">Days</th></tr></thead>
        <tbody>{corr_rows}</tbody></table>
        {cycle_block}
      </section>

      <section>
        <h2>30-day timeline</h2>
        <table><thead><tr><th>Date</th><th class="num">Pain</th><th class="num">Fatigue</th>
          <th class="num">Sleep</th><th class="num">Stress</th><th class="num">Cycle</th><th>Triggers</th></tr></thead>
        <tbody>{tl_rows}</tbody></table>
      </section>

      <section>
        <h2>Narrative summary</h2>
        <div class="narrative">{narrative}</div>
        <div class="nsrc">Generated by {esc(nsrc)}.</div>
      </section>
    </div>
    <div class="disc">
      <b>Not medical advice.</b> This is a patient-generated summary produced by EmberHealth
      from self-logged data and a transparent heuristic model. It is illustrative and is
      <b>not a diagnosis</b>. Always consult a qualified clinician for interpretation and care decisions.
    </div>
  </div>
</body></html>"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def forecast(logs, condition):
    """Transparent weighted model -> 0-100 risk for the next few days.

    Components (each contributes to the score with an explicit weight):
      * recent symptom momentum (last 3 days of pain/fatigue, decayed)
      * poor recent sleep + high stress
      * active known triggers in the last 2 days x trigger weight
      * cycle phase risk (pre-menstrual / menstrual) if condition is cycle-aware
    Everything is returned as `drivers` so the UI can explain the number.
    """
    if not logs:
        return {"risk": 0, "band": "unknown", "drivers": [], "trend": []}

    recent = logs[-5:]
    trig_defs = CONDITIONS[condition]["triggers"]
    drivers = []

    # 1) symptom momentum: exponentially weighted last-3 pain+fatigue
    last3 = logs[-3:]
    weights = [0.2, 0.3, 0.5][-len(last3):]
    wsum = sum(weights)
    pain_mom = sum(w * r["pain"] for w, r in zip(weights, last3)) / wsum
    fat_mom = sum(w * r["fatigue"] for w, r in zip(weights, last3)) / wsum
    sym_score = (pain_mom / 10 * 0.7 + fat_mom / 10 * 0.3) * 42
    drivers.append({"label": "Recent symptom momentum",
                    "detail": f"pain~{pain_mom:.1f}/10, fatigue~{fat_mom:.1f}/10",
                    "points": round(sym_score)})

    # 2) sleep + stress from last 3 days
    avg_sleep = sum(r["sleep"] for r in last3) / len(last3)
    avg_stress = sum(r["stress"] for r in last3) / len(last3)
    sleep_score = max(0, (6 - avg_sleep)) / 6 * 16
    stress_score = avg_stress / 10 * 14
    drivers.append({"label": "Sleep debt",
                    "detail": f"avg {avg_sleep:.1f}/10 hrs-quality",
                    "points": round(sleep_score)})
    drivers.append({"label": "Stress load",
                    "detail": f"avg {avg_stress:.1f}/10",
                    "points": round(stress_score)})

    # 3) active triggers in last 2 days
    active = {}
    for r in logs[-2:]:
        for t in json.loads(r["triggers"]):
            if t in trig_defs:
                active[t] = max(active.get(t, 0), trig_defs[t])
    trig_score = sum(active.values()) * 7.5
    trig_score = min(trig_score, 28)
    if active:
        drivers.append({
            "label": "Active triggers",
            "detail": ", ".join(pretty(t) for t in sorted(active, key=active.get, reverse=True)),
            "points": round(trig_score),
        })

    # 4) cycle phase
    cycle_score = 0
    if CONDITIONS[condition]["cycle_aware"] and recent[-1].get("cycle_day"):
        cd = recent[-1]["cycle_day"]
        # project forward 1-3 days
        upcoming = [((cd + k - 1) % 28) + 1 for k in range(1, 4)]
        if any(x >= 24 or x <= 4 for x in upcoming):
            cycle_score = 18
            drivers.append({"label": "Cycle phase (pre-menstrual window)",
                            "detail": f"cycle day ~{cd}, entering high-risk window",
                            "points": cycle_score})

    raw = sym_score + sleep_score + stress_score + trig_score + cycle_score
    risk = int(max(0, min(100, round(raw))))
    band = ("high" if risk >= 66 else "elevated" if risk >= 40 else "calm")

    # trend line: per-day composite over the last 30 days (for the chart)
    trend = [{"day": r["day"], "pain": r["pain"], "fatigue": r["fatigue"],
              "sleep": r["sleep"], "stress": r["stress"],
              "flare": 1 if r["pain"] >= 6 else 0,
              "triggers": json.loads(r["triggers"])} for r in logs[-30:]]

    drivers = [d for d in drivers if d["points"] > 0]
    drivers.sort(key=lambda d: d["points"], reverse=True)
    return {"risk": risk, "band": band, "drivers": drivers, "trend": trend}


def pretty(t):
    return t.replace("_", " ")


# ----------------------------------------------------------------------------
# AI (real Claude if key present, else MOCK)
# ----------------------------------------------------------------------------
def load_api_key():
    env = os.path.join(os.path.expanduser("~"), "creator-os", ".env")
    if not os.path.exists(env):
        return None
    try:
        with open(env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY"):
                    _, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
    except Exception:
        return None
    return None


def call_claude(system, user, max_tokens=700):
    key = load_api_key()
    if not key:
        return None
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            parts = [b.get("text", "") for b in data.get("content", [])]
            return "".join(parts).strip()
    except Exception as e:
        return None


def ai_insight(condition, fc, patterns, kind="insight"):
    label = CONDITIONS[condition]["label"]
    top = [p for p in patterns if p["precede_pct"] > 0][:3]
    pattern_txt = "; ".join(
        f"{pretty(p['trigger'])} precedes {p['precede_pct']}% of flares" for p in top
    ) or "no strong trigger pattern yet"
    driver_txt = "; ".join(f"{d['label']} (+{d['points']})" for d in fc["drivers"]) or "none"

    if kind == "report":
        system = ("You are a calm, warm clinical scribe helping a patient with an "
                  "under-served chronic condition prepare for a doctor visit. Write a "
                  "concise, structured, doctor-ready summary. Be factual, use the data, "
                  "never invent numbers. End with: 'This is a patient-generated summary, "
                  "not a diagnosis.'")
        user = (f"Condition: {label}. Current next-3-day flare risk: {fc['risk']}/100 "
                f"({fc['band']}). Risk drivers: {driver_txt}. Longitudinal patterns over "
                f"30 days: {pattern_txt}. Produce a doctor-ready report with sections: "
                f"Summary, Symptom trend, Likely triggers, Questions for my doctor.")
        mock = _mock_report(label, fc, top)
    else:
        system = ("You are EmberHealth, a warm, validating health companion for people "
                  "with often-dismissed chronic conditions. Speak in plain, gentle, "
                  "empowering English. 3-4 short sentences. No jargon. Never dismissive. "
                  "This is not medical advice.")
        user = (f"Condition: {label}. Next-3-day flare risk {fc['risk']}/100 ({fc['band']}). "
                f"Drivers: {driver_txt}. Patterns: {pattern_txt}. Give an empathetic, "
                f"specific insight and one gentle, actionable suggestion.")
        mock = _mock_insight(label, fc, top)

    out = call_claude(system, user)
    if out:
        return {"text": out, "source": "claude"}
    return {"text": mock, "source": "mock"}


def _mock_insight(label, fc, top):
    band = fc["band"]
    lead = {
        "high": f"Your body is signaling a likely {label} flare in the next few days.",
        "elevated": f"There's a moderate build-up of {label} flare risk right now.",
        "calm": f"Things look relatively steady for your {label} right now.",
    }[band]
    drv = fc["drivers"][0]["label"].lower() if fc["drivers"] else "recent symptoms"
    trg = pretty(top[0]["trigger"]) if top else "your usual triggers"
    return (f"{lead} The biggest factor is {drv}, and historically {trg} tends to "
            f"precede your flares. This isn't your fault — patterns like this are real "
            f"and measurable. A gentle step today: protect your sleep tonight and "
            f"pre-empt {trg} where you can. (Not medical advice.)")


def _mock_report(label, fc, top):
    trg_lines = "\n".join(
        f"  - {pretty(p['trigger'])}: precedes {p['precede_pct']}% of recorded flares"
        for p in top) or "  - No dominant trigger identified yet"
    drv = "\n".join(f"  - {d['label']}: +{d['points']} risk pts ({d['detail']})"
                    for d in fc["drivers"]) or "  - Insufficient data"
    return (
        f"DOCTOR-READY SUMMARY — {label}\n\n"
        f"SUMMARY\n"
        f"Patient is self-tracking a {label} flare pattern. Current model-estimated "
        f"flare risk for the next 3 days is {fc['risk']}/100 ({fc['band']}).\n\n"
        f"SYMPTOM TREND\n"
        f"Daily pain, fatigue, sleep quality and stress logged for 30 days. Current risk "
        f"drivers:\n{drv}\n\n"
        f"LIKELY TRIGGERS (longitudinal)\n{trg_lines}\n\n"
        f"QUESTIONS FOR MY DOCTOR\n"
        f"  - Do these trigger patterns fit a typical {label} presentation?\n"
        f"  - Should we consider a targeted elimination trial for the top trigger?\n"
        f"  - Are there preventive options to try before the pre-flare window?\n\n"
        f"This is a patient-generated summary, not a diagnosis."
    )


def ambient_preview(condition, fc):
    """Simulated wearable feed -> proactive ambient agent action (2036 vibe)."""
    random.seed()
    hrv = random.randint(28, 62)          # ms, lower = more strain
    resting_hr = random.randint(58, 82)
    sleep_eff = random.randint(58, 92)     # %
    skin_temp = round(random.uniform(-0.4, 0.9), 1)  # deviation C
    # ambient risk blends wearable strain with model risk
    wearable_strain = max(0, (60 - hrv)) / 32 * 40 + max(0, (75 - sleep_eff)) / 40 * 30
    amb_risk = int(min(100, round(0.55 * fc["risk"] + 0.45 * wearable_strain)))
    lead_hours = 24 + int((amb_risk / 100) * 48)
    actions = []
    if amb_risk >= 55:
        actions = [
            f"Pre-drafted a message to your care team flagging a probable flare in ~{lead_hours}h.",
            "Auto-adjusted tomorrow's calendar: moved 2 low-priority meetings, added rest blocks.",
            "Pre-ordered anti-inflammatory groceries and refilled your rescue plan.",
            "Queued a gentle mobility + heat-therapy routine for tonight.",
        ]
    elif amb_risk >= 35:
        actions = [
            f"Watching closely — early strain signals suggest possible flare in ~{lead_hours}h.",
            "Suggested an earlier wind-down tonight to bank recovery.",
            "Softened tomorrow morning's schedule as a precaution.",
        ]
    else:
        actions = [
            "No pre-emptive action needed. Recovery signals look healthy.",
            "Maintaining your current plan and protecting your good streak.",
        ]
    return {
        "wearable": {"hrv_ms": hrv, "resting_hr": resting_hr,
                     "sleep_efficiency_pct": sleep_eff, "skin_temp_dev_c": skin_temp},
        "ambient_risk": amb_risk,
        "lead_hours": lead_hours,
        "actions": actions,
    }


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, payload, ctype="application/json"):
        if ctype == "application/json":
            body = json.dumps(payload).encode()
        else:
            body = payload if isinstance(payload, bytes) else payload.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, {"error": "index.html missing"})
            return
        if self.path == "/api/config":
            self._send(200, {
                "conditions": {k: {"label": v["label"],
                                   "triggers": list(v["triggers"].keys()),
                                   "cycle_aware": v["cycle_aware"]}
                               for k, v in CONDITIONS.items()},
                "ai": "claude" if load_api_key() else "mock",
            })
            return
        # --- doctor-ready report (print/Save-as-PDF) ---
        m = re.match(r"^/report(?:\?condition=(\w+))?$", self.path)
        if m:
            condition = m.group(1) or "pcos"
            if condition not in CONDITIONS:
                condition = "pcos"
            data = build_report_data(condition)
            self._send(200, render_report_html(data), "text/html; charset=utf-8")
            return
        m = re.match(r"^/api/report_data(?:\?condition=(\w+))?$", self.path)
        if m:
            condition = m.group(1) or "pcos"
            if condition not in CONDITIONS:
                condition = "pcos"
            self._send(200, build_report_data(condition))
            return
        m = re.match(r"^/api/correlations(?:\?condition=(\w+))?$", self.path)
        if m:
            condition = m.group(1) or "pcos"
            if condition not in CONDITIONS:
                condition = "pcos"
            conn = connect()
            logs = _load_logs(conn, condition)
            conn.close()
            self._send(200, compute_correlations(logs, condition))
            return
        m = re.match(r"^/api/state(?:\?condition=(\w+))?$", self.path)
        if m:
            condition = m.group(1) or "pcos"
            if condition not in CONDITIONS:
                condition = "pcos"
            conn = connect()
            logs = _load_logs(conn, condition)
            conn.close()
            fc = forecast(logs, condition)
            patterns = compute_pattern_intelligence(logs, condition)
            self._send(200, {"condition": condition, "forecast": fc,
                             "patterns": patterns, "count": len(logs)})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            self._send(400, {"error": "bad json"})
            return

        if self.path == "/api/log":
            condition = data.get("condition", "pcos")
            if condition not in CONDITIONS:
                self._send(400, {"error": "unknown condition"})
                return
            day = data.get("day") or dt.date.today().isoformat()
            triggers = [t for t in data.get("triggers", []) if isinstance(t, str)]
            conn = connect()
            # upsert-ish: replace an existing same-day/condition log
            conn.execute("DELETE FROM logs WHERE day=? AND condition=?", (day, condition))
            conn.execute(
                "INSERT INTO logs (day, condition, pain, fatigue, sleep, stress, "
                "cycle_day, triggers, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (day, condition,
                 clamp(data.get("pain", 0)), clamp(data.get("fatigue", 0)),
                 clamp(data.get("sleep", 5)), clamp(data.get("stress", 0)),
                 data.get("cycle_day"), json.dumps(triggers), data.get("note", "")[:400]),
            )
            conn.commit()
            logs = _load_logs(conn, condition)
            conn.close()
            fc = forecast(logs, condition)
            patterns = compute_pattern_intelligence(logs, condition)
            self._send(200, {"ok": True, "condition": condition,
                             "forecast": fc, "patterns": patterns, "count": len(logs)})
            return

        if self.path == "/api/insight" or self.path == "/api/report":
            condition = data.get("condition", "pcos")
            if condition not in CONDITIONS:
                condition = "pcos"
            conn = connect()
            logs = _load_logs(conn, condition)
            conn.close()
            fc = forecast(logs, condition)
            patterns = compute_pattern_intelligence(logs, condition)
            kind = "report" if self.path.endswith("report") else "insight"
            res = ai_insight(condition, fc, patterns, kind=kind)
            self._send(200, res)
            return

        if self.path == "/api/ambient":
            condition = data.get("condition", "pcos")
            if condition not in CONDITIONS:
                condition = "pcos"
            conn = connect()
            logs = _load_logs(conn, condition)
            conn.close()
            fc = forecast(logs, condition)
            self._send(200, ambient_preview(condition, fc))
            return

        self._send(404, {"error": "not found"})


def clamp(v, lo=0, hi=10):
    try:
        return int(max(lo, min(hi, int(v))))
    except Exception:
        return lo


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"EmberHealth running -> http://localhost:{PORT}  "
          f"(AI: {'claude' if load_api_key() else 'mock'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
