#!/usr/bin/env python3
"""Presentation finale: boot the whole fleet, fire the autopilots, then narrate the Command Center
with the live global agent feed. Reuses the narration-through-browser pipeline."""
import os, time, json, base64, subprocess, signal, urllib.request
from playwright.sync_api import sync_playwright

ROOT="/home/sagar-uniyal/venture-lab/apps"; VOICE="JBFqnCBsd6RMkjVDRZzb"; MODEL="eleven_turbo_v2_5"
KEY=[l.split("=",1)[1].strip().strip('"') for l in open(os.path.expanduser("~/creator-os/.env")) if l.startswith("ELEVENLABS_API_KEY=")][0]
FLEET=[("1-tradepilot",8801),("2-emberhealth",8802),("3-claimguard",8803),("4-freightnormal",8804),
       ("5-closeloop",8805),("6-ledgerbridge",8806),("7-lorekeeper",8807),("8-compliagent",8808),
       ("9-focusforge",8809),("10-mesa",8810),("0-command-center",8811)]
AUTOPILOTS=[8801,8803,8804,8806,8807,8808,8810]
def up(p):
    try: urllib.request.urlopen(f"http://localhost:{p}/",timeout=1); return True
    except Exception: return False
def post(p,path):
    try: urllib.request.urlopen(urllib.request.Request(f"http://localhost:{p}{path}",data=b"{}",headers={"Content-Type":"application/json"},method="POST"),timeout=8).read()
    except Exception: pass
def tts(text):
    body=json.dumps({"text":text,"model_id":MODEL,"voice_settings":{"stability":0.45,"similarity_boost":0.8,"style":0.25}}).encode()
    r=urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",data=body,headers={"xi-api-key":KEY,"Content-Type":"application/json","Accept":"audio/mpeg"})
    try: return base64.b64encode(urllib.request.urlopen(r,timeout=30).read()).decode()
    except Exception: return ""
BAR="""()=>{if(document.getElementById('__nb'))return;const b=document.createElement('div');b.id='__nb';
 b.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:20px 26px;font:600 21px/1.4 Segoe UI,sans-serif;color:#fff;text-align:center;background:linear-gradient(0deg,rgba(6,5,12,.96),rgba(6,5,12,.72));border-top:2px solid rgba(168,85,247,.7);text-shadow:0 2px 12px #000';
 const s=document.createElement('div');s.id='__nbg';s.style.cssText='font:700 11px/1 ui-monospace;letter-spacing:.3em;color:#c084fc;margin-bottom:8px;text-transform:uppercase';b.appendChild(s);
 const t=document.createElement('div');t.id='__nbt';b.appendChild(t);document.body.appendChild(b);}"""
def narrate(pg,text,tag):
    b64=tts(text); pg.evaluate(BAR)
    pg.evaluate("""async(a)=>{const[b64,cap,tag]=a;document.getElementById('__nbt').textContent=cap;document.getElementById('__nbg').textContent=tag;
      if(!b64)return await new Promise(r=>setTimeout(r,900+cap.length*45));const au=new Audio('data:audio/mpeg;base64,'+b64);
      try{await au.play();}catch(e){}await new Promise(r=>{au.onended=r;au.onerror=r;setTimeout(r,15000);});}""",[b64,text,tag])

subprocess.run("rm -f "+ROOT+"/*/*.db",shell=True)
procs=[subprocess.Popen(["python3","server.py"],cwd=os.path.join(ROOT,d),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for d,_ in FLEET]
for _ in range(30):
    if sum(up(p) for _,p in FLEET)>=len(FLEET): break
    time.sleep(0.6)
print("fleet up:",sum(up(p) for _,p in FLEET),"/",len(FLEET))
for _ in range(4):
    for p in AUTOPILOTS: post(p,"/api/autopilot/step")
    time.sleep(0.3)
try:
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=False,args=["--start-maximized","--autoplay-policy=no-user-gesture-required"])
        pg=b.new_context(viewport=None).new_page(); pg.goto("http://localhost:8811/",wait_until="networkidle",timeout=20000); pg.wait_for_timeout(1500)
        narrate(pg,"And this is where it all comes together — the Command Center. Every one of the ten products is reporting in live, right now.","Finale · Command Center")
        narrate(pg,"Each tile is a real product with a real database — dollars collected, flare risk, compliance score, creator payouts — all polled concurrently in real time.","The live fleet")
        pg.evaluate("window.scrollTo(0,document.body.scrollHeight)"); pg.wait_for_timeout(1500)
        narrate(pg,"But here is the real story. Below is the Autonomous Operations feed. Seven of these products are running their own agents right now — booking freight, filing compliance, re‑engaging fans, reconciling ledgers.","Autonomous operations")
        narrate(pg,"This is the whole thesis in one screen: proprietary data in every vertical, and agents acting on it autonomously. Ten businesses — running themselves. That is what we built tonight.","The thesis, realised")
        pg.wait_for_timeout(1500); b.close()
finally:
    for pr in procs:
        try: pr.send_signal(signal.SIGINT)
        except Exception: pass
    time.sleep(1)
    for pr in procs:
        try: pr.terminate()
        except Exception: pass
    print("finale complete, fleet down")
