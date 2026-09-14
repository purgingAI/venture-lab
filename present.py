#!/usr/bin/env python3
"""Narrated live walkthrough of the Venture Lab portfolio.
ElevenLabs voice is played THROUGH the headed browser (in sync with on-screen actions), so it's a
real side-by-side presentation. Each app: boot -> tour with narration -> teardown. Grand finale =
the Command Center with all autopilots firing.
"""
import os, time, json, base64, subprocess, signal, urllib.request
from playwright.sync_api import sync_playwright

ROOT = "/home/sagar-uniyal/venture-lab/apps"
VOICE = "JBFqnCBsd6RMkjVDRZzb"        # George — warm British narrator
MODEL = "eleven_turbo_v2_5"
KEY = None
for ln in open(os.path.expanduser("~/creator-os/.env")):
    if ln.startswith("ELEVENLABS_API_KEY="): KEY = ln.split("=",1)[1].strip().strip('"').strip("'")

_cache = {}
def tts_b64(text):
    if text in _cache: return _cache[text]
    body = json.dumps({"text": text, "model_id": MODEL,
                       "voice_settings": {"stability":0.45,"similarity_boost":0.8,"style":0.25}}).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",
        data=body, headers={"xi-api-key":KEY,"Content-Type":"application/json","Accept":"audio/mpeg"})
    try:
        b = urllib.request.urlopen(req, timeout=30).read()
        out = base64.b64encode(b).decode(); _cache[text]=out; return out
    except Exception as e:
        print("tts err", e); return ""

def up(port):
    try: urllib.request.urlopen(f"http://localhost:{port}/",timeout=1); return True
    except Exception: return False
def boot(d):
    return subprocess.Popen(["python3","server.py"], cwd=os.path.join(ROOT,d),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def post(port,path):
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://localhost:{port}{path}",data=b"{}",
            headers={"Content-Type":"application/json"},method="POST"),timeout=8).read()
    except Exception: pass

# inject a narration bar once
BAR_JS = """()=>{ if(document.getElementById('__nb'))return;
  const b=document.createElement('div'); b.id='__nb';
  b.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:20px 26px;'+
   'font:600 21px/1.4 Segoe UI,system-ui,sans-serif;color:#fff;text-align:center;'+
   'background:linear-gradient(0deg,rgba(6,5,12,.96),rgba(6,5,12,.72));'+
   'border-top:2px solid rgba(168,85,247,.7);backdrop-filter:blur(8px);text-shadow:0 2px 12px #000';
  const t=document.createElement('div'); t.id='__nbt'; b.appendChild(t);
  const s=document.createElement('div'); s.id='__nbtag'; s.style.cssText='font:700 11px/1 ui-monospace;letter-spacing:.3em;color:#c084fc;margin-bottom:8px;text-transform:uppercase'; b.insertBefore(s,t);
  document.body.appendChild(b); }"""

def narrate(page, text, tag=""):
    b64 = tts_b64(text)
    page.evaluate(BAR_JS)
    page.evaluate("""async (a)=>{const[b64,cap,tag]=a;
      document.getElementById('__nbt').textContent=cap;
      document.getElementById('__nbtag').textContent=tag;
      if(!b64)return await new Promise(r=>setTimeout(r,Math.min(6000,900+cap.length*45)));
      const au=new Audio('data:audio/mpeg;base64,'+b64);
      try{await au.play();}catch(e){}
      await new Promise(r=>{au.onended=r;au.onerror=r;setTimeout(r,15000);}); }""", [b64, text, tag])

def click(page, text):
    try: page.get_by_text(text, exact=False).first.click(timeout=2500)
    except Exception:
        try: page.get_by_role("button", name=text).first.click(timeout=1500)
        except Exception: pass
def scroll(page, frac):
    try: page.evaluate("f=>window.scrollTo({top:document.body.scrollHeight*f,behavior:'smooth'})", frac)
    except Exception: pass

# ---- per-app scripts: list of (kind, arg[, arg2]) ----
TOURS = {
 "1-tradepilot": ("TradePilot",[
   ("say","This is TradePilot — a job‑to‑cash cockpit for solo tradespeople like plumbers and electricians. Existing tools are built for big firms; this one is for a team of one.","App 1 of 10 · TradePilot"),
   ("say","Up top is the money strip — collected, deposits held, outstanding, and the platform fee. Real numbers from a live database.",""),
   ("scroll",0.4),("say","Here is the pipeline: every job moves from new, to quoted, to scheduled, invoiced, and paid. A tap advances it.",""),
   ("scroll",0.0),("click","AUTOPILOT"),("say","And this is the future made real. I switch on Autopilot — and it reads an incoming customer message, drafts a full itemised quote with the AI, and creates the job hands‑free.","Live Autopilot"),
   ("wait",5000),("say","That is the moat: a pricing brain that learns, and an agent that turns a text message into a booked job.",""),
 ]),
 "3-claimguard": ("ClaimGuard",[
   ("say","ClaimGuard catches medical billing errors before a claim is ever submitted — the single most underserved gap we found, for small practices.","App 2 · ClaimGuard"),
   ("say","Each claim is scanned against a denial‑pattern engine. It flags coding mistakes and shows the dollars at risk, with a plain‑English fix.",""),
   ("click","Batch Scan"),("say","In Batch mode I can run a whole stack of claims at once and see the aggregate exposure.","Batch scan"),
   ("wait",1500),("click","Analytics"),("say","Analytics reveals the trends — which payers deny most, and how much revenue we've protected over time.","Analytics"),
   ("scroll",0.2),("click","AUTOPILOT"),("say","And Autopilot auto‑appeals the safe, mechanical fixes while queueing the high‑value claims for a human — recovering revenue while you sleep.","Auto‑appeal"),("wait",4000),
 ]),
 "9-focusforge": ("FocusForge",[
   ("say","FocusForge is an executive‑function operating system built specifically for A.D.H.D. brains — not generic mindfulness.","App 3 · FocusForge"),
   ("say","The Momentum dashboard shows your streak, completion rate, and your best focus window — concrete feedback the A.D.H.D. brain responds to.",""),
   ("scroll",0.5),("say","The core idea is beating task‑initiation paralysis: any task expands into a two‑minute starter step, generated by the AI.","2‑minute starter"),
   ("scroll",0.9),("say","And a body‑doubling timer makes time visible with a shrinking ring — fighting time‑blindness, with a dopamine burst on completion.",""),
 ]),
 "2-emberhealth": ("EmberHealth",[
   ("say","EmberHealth predicts flares for conditions the app world ignores — P.C.O.S., endometriosis, fibromyalgia.","App 4 · EmberHealth"),
   ("say","You log symptoms and triggers, and a transparent model forecasts your flare risk for the next few days, and tells you which triggers are driving it.",""),
   ("scroll",0.55),("say","Below, a Correlations panel finds the patterns a doctor might miss — for example, that dairy precedes most of your flares.","Correlations"),
   ("scroll",0.15),("say","And with one click it exports a clinician‑ready report — turning months of data into a document your doctor can act on.",""),
 ]),
 "4-freightnormal": ("FreightNormal",[
   ("say","FreightNormal untangles freight quotes. Carrier rates arrive as messy emails; this normalises any quote into a clean, comparable schedule.","App 5 · FreightNormal"),
   ("say","Paste a chaotic quote and the AI extracts linehaul, fuel, accessorials, and a true per‑mile rate — instantly comparable across carriers.",""),
   ("scroll",0.4),("click","AUTOPILOT"),("say","Autopilot watches open lanes and auto‑books the cheapest carrier the moment the margin clears your threshold — generating the rate confirmation itself.","Auto‑book"),("wait",4000),
 ]),
 "5-closeloop": ("CloseLoop",[
   ("say","CloseLoop reconciles real‑estate closings. Earnest money, commissions, and fees drift constantly; this catches it before funding.","App 6 · CloseLoop"),
   ("say","Each transaction gets an expected‑versus‑actual ledger, with every mismatch highlighted and a running out‑of‑balance figure.",""),
   ("scroll",0.5),("say","A funding‑readiness checklist gives a clear ‘clear‑to‑fund’ verdict, and one click exports the full closing packet.",""),
 ]),
 "6-ledgerbridge": ("LedgerBridge",[
   ("say","LedgerBridge is the connective tissue for a small accounting firm's sixty‑odd disconnected tools.","App 7 · LedgerBridge"),
   ("say","This animated node‑map shows each system and how their accounts map together — solid lines are mapped, dashed red are not.",""),
   ("scroll",0.3),("click","AUTOPILOT"),("say","Autopilot auto‑maps the confident accounts and clears the small drift continuously, queueing anything ambiguous for the accountant.","Auto‑reconcile"),("wait",4000),
 ]),
 "7-lorekeeper": ("LoreKeeper",[
   ("say","LoreKeeper lets creators own their super‑fan monetisation, instead of relying on platform payouts.","App 8 · LoreKeeper"),
   ("say","This constellation is the fan graph — each node a super‑fan, sized by spend, glowing by affinity. That graph is the moat.",""),
   ("scroll",0.3),("say","There's an A.I. persona that chats to fans in the creator's own voice, and a revenue dashboard tracking every drop.","AI persona"),
   ("scroll",0.0),("click","AUTOPILOT"),("say","And Persona Autopilot finds cold, high‑value fans and re‑engages them automatically, in‑voice — the creator's presence, running itself.","Persona autopilot"),("wait",4000),
 ]),
 "8-compliagent": ("CompliAgent",[
   ("say","CompliAgent is a living compliance radar for regulated small businesses drowning in rule changes.","App 9 · CompliAgent"),
   ("say","This radar sweeps in regulatory changes, colour‑coded by domain, and maps each one onto your specific business as concrete obligations.",""),
   ("scroll",0.4),("click","AUTOPILOT"),("say","Autopilot auto‑files the routine obligations and produces a confirmation reference, while queueing the high‑stakes ones for human sign‑off.","Auto‑file"),("wait",4000),
   ("say","And it generates a full, audit‑grade compliance pack on demand.",""),
 ]),
 "10-mesa": ("Mesa",[
   ("say","Finally, Mesa — an ambient operational brain for the mid‑market, and it dog‑foods our own A.C.E. design.","App 10 · Mesa"),
   ("say","Anyone can simply ask the company a question, and it answers — grounded in the live operational graph of people, projects, and systems.","Ask the company"),
   ("scroll",0.5),("click","AUTOPILOT"),("say","Its Autopilot scans for operational risk, reasons about the fix, and applies the safe actions itself — queueing the risky ones for a human. This one was born ambient.","Autonomous ops"),("wait",4000),
 ]),
}

def run_app(pw, d):
    name, steps = TOURS[d]
    port = int(d.split("-")[0]); port = {"1":8801,"2":8802,"3":8803,"4":8804,"5":8805,"6":8806,"7":8807,"8":8808,"9":8809,"10":8810}[d.split("-")[0]]
    srv = boot(d)
    for _ in range(20):
        if up(port): break
        time.sleep(0.5)
    b = pw.chromium.launch(headless=False, args=["--start-maximized","--autoplay-policy=no-user-gesture-required"])
    pg = b.new_context(viewport=None).new_page()
    try:
        pg.goto(f"http://localhost:{port}/", wait_until="networkidle", timeout=20000)
        pg.wait_for_timeout(800)
        for st in steps:
            k = st[0]
            if k=="say": narrate(pg, st[1], st[2] if len(st)>2 else name)
            elif k=="click": click(pg, st[1]); pg.wait_for_timeout(700)
            elif k=="scroll": scroll(pg, st[1]); pg.wait_for_timeout(900)
            elif k=="wait": pg.wait_for_timeout(st[1])
    finally:
        b.close()
        try: srv.send_signal(signal.SIGINT)
        except Exception: pass
        time.sleep(0.4); srv.terminate()

if __name__=="__main__":
    order = ["1-tradepilot","3-claimguard","9-focusforge","2-emberhealth","4-freightnormal",
             "5-closeloop","6-ledgerbridge","7-lorekeeper","8-compliagent","10-mesa"]
    with sync_playwright() as pw:
        # intro on a blank page
        b0=pw.chromium.launch(headless=False,args=["--start-maximized","--autoplay-policy=no-user-gesture-required"])
        p0=b0.new_context(viewport=None).new_page(); p0.goto("about:blank")
        p0.evaluate("document.body.style.background='#05060b'")
        narrate(p0,"Welcome. Tonight we built ten vertical A.I. products — each for a market nobody serves well, each designed to become an autonomous agent. Let me walk you through them, one by one.","Venture Lab · Presentation")
        b0.close()
        for d in order:
            try: run_app(pw, d)
            except Exception as e: print("app err",d,e)
    print("presentation complete")
