# -*- coding: utf-8 -*-
import json, urllib.request
from playwright.sync_api import sync_playwright

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return None, str(e)

for ep in ["http://127.0.0.1:7871/api/status",
           "http://127.0.0.1:7871/api/env-check",
           "http://127.0.0.1:7871/api/models/check",
           "http://127.0.0.1:7871/api/torch/status"]:
    s, d = get(ep)
    if not s:
        # 尝试触发 env-check
        if "env-check" in ep:
            s, d = get("http://127.0.0.1:7871/api/env-check")
        print(f"### {ep}\n  -> {d}\n")
        continue
    print(f"### {ep}\n  -> {json.dumps(d, ensure_ascii=False)[:1500]}\n")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto("http://127.0.0.1:7871/")
    pg.wait_for_load_state('networkidle')
    pg.wait_for_timeout(1000)
    print("### PAGE TITLE:", pg.title())
    print("### BODY TEXT (first 2500):")
    print(pg.inner_text('body')[:2500])
    pg.screenshot(path="c:\\Users\\Doro\\SeedVR2-lite\\.trae-recon.png", full_page=True)
    print("### BUTTONS:", [t.strip() for t in pg.locator('button').all_inner_texts()][:40])
    print("### LINKS:", [t.strip() for t in pg.locator('a').all_inner_texts()][:20])
    b.close()