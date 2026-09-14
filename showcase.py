#!/usr/bin/env python3
"""Open each finished venture-lab app in a real (headed) browser so the user can see it,
screenshot it for verification, then shut its server down. Sequential to keep RAM low."""
import sys, os, time, subprocess, signal, urllib.request
from playwright.sync_api import sync_playwright

ROOT = "/home/sagar-uniyal/venture-lab/apps"
# name : dir : port  (extend as apps finish)
APPS = [
    ("TradePilot", "1-tradepilot", 8801),
    ("FocusForge", "9-focusforge", 8809),
    ("ClaimGuard", "3-claimguard", 8803),
]
if len(sys.argv) > 1:  # override: name=dir=port,name=dir=port
    APPS = [tuple(x.split("=")) for x in sys.argv[1].split(",")]
    APPS = [(n, d, int(p)) for n, d, p in APPS]

def up(port):
    try:
        urllib.request.urlopen(f"http://localhost:{port}/", timeout=2); return True
    except Exception:
        return False

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=150,
                                args=["--start-maximized", "--ignore-certificate-errors"])
    for name, d, port in APPS:
        appdir = os.path.join(ROOT, d)
        if not os.path.isdir(appdir):
            print(f"[{name}] dir missing, skip"); continue
        srv = subprocess.Popen(["python3", "server.py"], cwd=appdir,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(15):
            if up(port): break
            time.sleep(0.5)
        if not up(port):
            print(f"[{name}] did not come up on :{port}"); srv.terminate(); continue
        pg = browser.new_context(viewport=None, ignore_https_errors=True).new_page()
        try:
            pg.goto(f"http://localhost:{port}/", wait_until="networkidle", timeout=20000)
        except Exception:
            pg.goto(f"http://localhost:{port}/", timeout=20000)
        time.sleep(4.5)  # let the user watch it live
        shot = f"/tmp/vl_{d}.png"
        pg.screenshot(path=shot)
        print(f"[{name}] :{port} -> {shot}")
        pg.context.close()
        srv.send_signal(signal.SIGINT); time.sleep(0.5); srv.terminate()
        try: srv.wait(timeout=3)
        except Exception: srv.kill()
    browser.close()
print("showcase done")
