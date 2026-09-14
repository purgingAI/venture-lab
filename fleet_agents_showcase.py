#!/usr/bin/env python3
"""Boot the fleet, fire each app's Autopilot a few times to generate autonomous actions, then open
the Command Center headed showing the live global agent feed. Screenshot + tear down."""
import os, time, json, subprocess, signal, urllib.request
from playwright.sync_api import sync_playwright

ROOT = "/home/sagar-uniyal/venture-lab/apps"
FLEET = [("1-tradepilot",8801),("2-emberhealth",8802),("3-claimguard",8803),("4-freightnormal",8804),
         ("5-closeloop",8805),("6-ledgerbridge",8806),("7-lorekeeper",8807),("8-compliagent",8808),
         ("9-focusforge",8809),("10-mesa",8810),("0-command-center",8811)]
AUTOPILOTS = [8801, 8803, 8804, 8806, 8807, 8808, 8810]  # apps with /api/autopilot/step

def up(port):
    try: urllib.request.urlopen(f"http://localhost:{port}/",timeout=1); return True
    except Exception: return False
def post(port, path):
    try:
        r=urllib.request.Request(f"http://localhost:{port}{path}", data=b"{}",
            headers={"Content-Type":"application/json"}, method="POST")
        urllib.request.urlopen(r, timeout=8).read(); return True
    except Exception as e: return False

# fresh dbs so autopilots have work to do
subprocess.run("rm -f "+ROOT+"/*/*.db", shell=True)
procs=[(d,p,subprocess.Popen(["python3","server.py"],cwd=os.path.join(ROOT,d),
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)) for d,p in FLEET]
for _ in range(30):
    if sum(up(p) for _,p,_ in procs)>=len(FLEET): break
    time.sleep(0.6)
print("fleet up:", sum(up(p) for _,p,_ in procs), "/", len(FLEET))

# fire the autopilots to generate a cross-fleet activity stream
for _ in range(3):
    for port in AUTOPILOTS: post(port, "/api/autopilot/step")
    time.sleep(0.4)
print("autopilots fired")

try:
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=False, slow_mo=120, args=["--start-maximized"])
        pg=b.new_context(viewport=None).new_page()
        pg.goto("http://localhost:8811/",wait_until="networkidle",timeout=20000)
        pg.wait_for_timeout(1500)
        # scroll to the Autonomous Operations panel
        pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(7)
        pg.screenshot(path="/tmp/vl_agent_feed.png", full_page=True); print("shot /tmp/vl_agent_feed.png")
        b.close()
finally:
    for d,p,pr in procs:
        try: pr.send_signal(signal.SIGINT)
        except Exception: pass
    time.sleep(1)
    for d,p,pr in procs:
        try: pr.terminate()
        except Exception: pass
    print("torn down")
