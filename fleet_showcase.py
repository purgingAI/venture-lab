#!/usr/bin/env python3
"""Boot the whole fleet (10 apps + Command Center), open the Command Center headed so the user
sees all 10 reporting live, screenshot it, then tear everything down."""
import os, time, subprocess, signal, urllib.request
from playwright.sync_api import sync_playwright

ROOT = "/home/sagar-uniyal/venture-lab/apps"
FLEET = [("1-tradepilot",8801),("2-emberhealth",8802),("3-claimguard",8803),("4-freightnormal",8804),
         ("5-closeloop",8805),("6-ledgerbridge",8806),("7-lorekeeper",8807),("8-compliagent",8808),
         ("9-focusforge",8809),("10-mesa",8810),("0-command-center",8811)]
def up(port):
    try: urllib.request.urlopen(f"http://localhost:{port}/",timeout=1); return True
    except Exception: return False

procs=[]
for d,p in FLEET:
    procs.append((d,p,subprocess.Popen(["python3","server.py"],cwd=os.path.join(ROOT,d),
                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)))
# wait for the fleet to come up
for _ in range(30):
    live=sum(1 for d,p,_ in procs if up(p))
    if live>=len(FLEET): break
    time.sleep(0.6)
print("fleet up:", sum(1 for d,p,_ in procs if up(p)), "/", len(FLEET))
try:
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=False, slow_mo=120, args=["--start-maximized"])
        pg=b.new_context(viewport=None).new_page()
        pg.goto("http://localhost:8811/",wait_until="networkidle",timeout=20000)
        time.sleep(8)   # let the 5s auto-refresh poll the live fleet + user watch
        pg.screenshot(path="/tmp/vl_command_center.png"); print("shot /tmp/vl_command_center.png")
        b.close()
finally:
    for d,p,pr in procs:
        try: pr.send_signal(signal.SIGINT)
        except Exception: pass
    time.sleep(1)
    for d,p,pr in procs:
        try: pr.terminate()
        except Exception: pass
    print("fleet torn down")
