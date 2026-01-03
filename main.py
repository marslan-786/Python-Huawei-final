import os
import glob
import asyncio
import random
import time
import shutil
import imageio
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- 1. INITIALIZE APP ---
app = FastAPI()

# --- CONFIGURATION ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# --- IMPORT SOLVER ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    print("❌ ERROR: captcha_solver.py not found!")
    async def solve_captcha(page, session_id, logger=print): return False

# --- GLOBAL SETTINGS ---
SETTINGS = {
    "country": "Russia",
    "proxy_manual": "",
}

BOT_RUNNING = False
logs = []

def log_msg(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

# --- STRICT PROXY PARSER ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    
    # FORMAT: IP:PORT:USER:PASS
    if p.count(":") == 3 and "://" not in p:
        parts = p.split(":")
        return {
            "server": f"http://{parts[0]}:{parts[1]}",
            "username": parts[2],
            "password": parts[3]
        }
    
    # FORMAT: URL
    if "://" not in p: p = f"http://{p}"
    try:
        parsed = urlparse(p)
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username: cfg["username"] = parsed.username
        if parsed.password: cfg["password"] = parsed.password
        return cfg
    except Exception as e:
        log_msg(f"⚠️ Proxy Parse Error: {e}")
        return None

def get_strict_proxy():
    # 1. Manual
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"].strip()) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    # 2. File
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy_string(random.choice(lines))
        except: pass
    return None

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f:
            lines = f.read().splitlines()
        for num in lines:
            if num.strip(): return num.strip()
    return None

# --- API ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)
    images = [f"/captures/{os.path.basename(f)}" for f in files[:10]]
    prox = get_strict_proxy()
    p_disp = prox['server'] if prox else "❌ NO PROXY"
    return JSONResponse({"logs": logs[:50], "images": images, "running": BOT_RUNNING, "current_country": SETTINGS["country"], "current_proxy": p_disp})

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country
    SETTINGS["proxy_manual"] = manual_proxy
    return {"status": "updated"}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "saved"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Numbers File Uploaded")
    return {"status": "saved"}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING:
        BOT_RUNNING = True
        bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 STOP COMMAND RECEIVED.")
    return {"status": "stopping"}

# --- VISUALS ---
async def capture_step(page, step_name, wait_time=0):
    if not BOT_RUNNING: return
    if wait_time > 0: await asyncio.sleep(wait_time)
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{CAPTURE_DIR}/{timestamp}_{step_name}.jpg"
    try: await page.screenshot(path=filename)
    except: pass

async def show_red_dot(page, x, y):
    try:
        await page.evaluate(f"""
            var dot = document.createElement('div');
            dot.style.position = 'absolute'; 
            dot.style.left = '{x-15}px'; dot.style.top = '{y-15}px';
            dot.style.width = '30px'; dot.style.height = '30px'; 
            dot.style.background = 'rgba(255, 0, 0, 0.9)'; 
            dot.style.borderRadius = '50%'; dot.style.zIndex = '2147483647'; 
            dot.style.pointerEvents = 'none'; dot.style.border = '3px solid white'; 
            document.body.appendChild(dot);
            setTimeout(() => {{ dot.remove(); }}, 1000);
        """)
    except: pass

# --- 🔥 STRATEGY LOGIC (1 to 5) 🔥 ---
async def execute_strategy(page, element, strategy_id, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if not box: return False
        
        # Center Coords
        cx = box['x'] + box['width'] / 2
        cy = box['y'] + box['height'] / 2
        
        # Right Side Coords (For Arrow/List)
        rx = box['x'] + box['width'] - 30
        ry = cy

        # --- LOGIC 1: STANDARD CLICK ---
        if strategy_id == 1:
            log_msg(f"🔹 Logic 1 (Standard): {desc}")
            await element.click(force=True, timeout=2000)

        # --- LOGIC 2: JS CLICK (Ghost) ---
        elif strategy_id == 2:
            log_msg(f"🔹 Logic 2 (JS Force): {desc}")
            await element.evaluate("e => e.click()")

        # --- LOGIC 3: VISUAL TAP (Center) ---
        elif strategy_id == 3:
            log_msg(f"🔹 Logic 3 (Tap Center): {desc}")
            await show_red_dot(page, cx, cy)
            await page.touchscreen.tap(cx, cy)

        # --- LOGIC 4: VISUAL TAP (Right Edge - Critical for Country) ---
        elif strategy_id == 4:
            log_msg(f"🔹 Logic 4 (Tap Right): {desc}")
            await show_red_dot(page, rx, ry)
            await page.touchscreen.tap(rx, ry)

        # --- LOGIC 5: CDP RAW TOUCH (Hammer) ---
        elif strategy_id == 5:
            log_msg(f"🔹 Logic 5 (CDP Hammer): {desc}")
            await show_red_dot(page, cx, cy)
            client = await page.context.new_cdp_session(page)
            await client.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx, "y": cy}]})
            await asyncio.sleep(0.1)
            await client.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

        return True
    except Exception as e:
        # log_msg(f"⚠️ Strategy {strategy_id} Error: {e}")
        return False

async def secure_step(page, finder_func, success_check, step_name, pre_action=None):
    # Loop 1 to 5 Strategies
    for attempt in range(1, 6):
        if not BOT_RUNNING: return False
        
        # 1. Check if already succeeded
        try:
            if await success_check().count() > 0: return True
        except: pass
        
        # 2. Try Current Logic
        try:
            btn = finder_func()
            if await btn.count() > 0:
                if attempt > 1: log_msg(f"♻️ {step_name}: Logic {attempt}/5...")
                
                # Pre-action (like checkbox) also uses a simple tap
                if pre_action: await pre_action()
                
                # Execute Logic based on Loop Index
                await execute_strategy(page, btn.first, attempt, step_name)
                
                await asyncio.sleep(0.5) 
                await capture_step(page, f"{step_name}_L{attempt}", wait_time=0)
                await asyncio.sleep(2.5) # Wait for page reaction
            else:
                if attempt == 1: log_msg(f"⏳ Searching {step_name}...")
                await asyncio.sleep(2)
        except Exception: pass
    
    log_msg(f"❌ Failed: {step_name}")
    await capture_step(page, f"Stuck_{step_name}", wait_time=0)
    return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    
    # Proxy Check
    if not get_strict_proxy():
        log_msg("⛔ FATAL: Proxy Required! Check proxies.txt or settings.")
        BOT_RUNNING = False
        return

    log_msg("🟢 Worker Started.")
    
    while BOT_RUNNING:
        current_number = get_next_number()
        if not current_number:
            log_msg("ℹ️ No Numbers left.")
            BOT_RUNNING = False; break
            
        proxy_cfg = get_strict_proxy()
        if not proxy_cfg:
            log_msg("⛔ Proxy Error. Stopping.")
            BOT_RUNNING = False; break
            
        p_show = proxy_cfg['server']
        log_msg(f"🔵 Processing: {current_number} | Proxy: {p_show}")
        
        try:
            res = await run_session(current_number, SETTINGS["country"], proxy_cfg)
            if res == "success": log_msg("🎉 Verified!")
            else: log_msg("❌ Failed/Skipped.")
        except Exception as e:
            log_msg(f"🔥 Crash: {e}")
        
        await asyncio.sleep(2)

async def run_session(phone, country, proxy):
    try:
        async with async_playwright() as p:
            launch_args = {"headless": True, "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"]}
            launch_args["proxy"] = proxy # STRICT PROXY USE

            log_msg("🚀 Launching Browser...")
            try:
                browser = await p.chromium.launch(**launch_args)
            except Exception as e:
                log_msg(f"❌ Proxy Connection Fail: {e}")
                return "retry"

            # 412x950 as requested
            pixel_5 = p.devices['Pixel 5'].copy()
            pixel_5['viewport'] = {'width': 412, 'height': 950}
            pixel_5['has_touch'] = True 
            
            context = await browser.new_context(**pixel_5, locale="en-US")
            page = await context.new_page()

            log_msg("🌐 Loading...")
            try:
                if not BOT_RUNNING: return "stopped"
                await page.goto(BASE_URL, timeout=45000)
                await capture_step(page, "01_Loaded", wait_time=2)

                # 1. REGISTER
                if not await secure_step(
                    page, 
                    lambda: page.get_by_text("Register", exact=True), 
                    lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), 
                    "Register"
                ): await browser.close(); return "retry"

                # 2. AGREE
                cb_text = page.get_by_text("stay informed", exact=False).first
                async def tick_box():
                    # Simple tap logic for box
                    if await cb_text.count() > 0: await execute_strategy(page, cb_text, 3, "Checkbox")

                if not await secure_step(
                    page,
                    lambda: page.get_by_role("button", name="Agree").or_(page.get_by_role("button", name="Next")),
                    lambda: page.get_by_text("Next", exact=True), 
                    "Agree_Btn",
                    pre_action=tick_box
                ): await browser.close(); return "retry"

                # 3. DOB
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                if not await secure_step(
                    page,
                    lambda: page.get_by_text("Next", exact=True),
                    lambda: page.get_by_text("Use phone number", exact=False),
                    "DOB_Next"
                ): await browser.close(); return "retry"

                # 4. USE PHONE
                if not await secure_step(
                    page,
                    lambda: page.get_by_text("Use phone number", exact=False),
                    lambda: page.get_by_text("Country/Region"), 
                    "UsePhone"
                ): await browser.close(); return "retry"

                # 5. COUNTRY SWITCH (The Logic will auto-escalate to Logic 4 which is Right-Tap)
                log_msg(f"🌍 Selecting {country}...")
                
                # We define success as Search Input appearing
                list_opened = await secure_step(
                    page,
                    lambda: page.locator(".hwid-list-item").filter(has_text="Country/Region").or_(page.get_by_text("Country/Region")),
                    lambda: page.get_by_placeholder("Search", exact=False),
                    "Open_Country_List"
                )
                
                if not list_opened:
                    # Last ditch effort: Blind Tap
                    log_msg("⚠️ Blind Tap Fallback...")
                    await page.touchscreen.tap(380, 200)
                    await asyncio.sleep(2)
                    if await page.get_by_placeholder("Search", exact=False).count() == 0:
                        log_msg("❌ List Open Failed")
                        await browser.close(); return "retry"

                # Search & Select
                search = page.get_by_placeholder("Search", exact=False).first
                await search.click()
                await page.keyboard.type(country, delay=50)
                await capture_step(page, "04_Typed", wait_time=2) 
                
                matches = page.get_by_text(country, exact=False)
                if await matches.count() > 1: await execute_strategy(page, matches.nth(1), 1, "Result")
                elif await matches.count() == 1: await execute_strategy(page, matches.first, 1, "Result")
                else: log_msg(f"❌ Country Not Found"); await browser.close(); return "retry"
                await capture_step(page, "05_Selected", wait_time=1)

                # 6. INPUT
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0: inp = page.locator("input").first
                
                if await inp.count() > 0:
                    log_msg("🔢 Inputting Phone...")
                    await inp.click()
                    for c in phone:
                        if not BOT_RUNNING: return "stopped"
                        await page.keyboard.type(c); await asyncio.sleep(0.05)
                    await page.touchscreen.tap(350, 100)
                    
                    # GET CODE (Using Logic Escalation)
                    if not await secure_step(
                        page,
                        lambda: page.locator(".get-code-btn").or_(page.get_by_text("Get code")),
                        lambda: page.get_by_text("swap 2 tiles", exact=False).or_(page.get_by_text("An unexpected problem", exact=False)),
                        "GET_CODE"
                    ): 
                        # Sometimes transition is silent, assume success if button gone?
                        pass

                    await asyncio.sleep(2)
                    if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                        log_msg("⛔ FATAL: Not Supported")
                        await capture_step(page, "Error_Popup", wait_time=0)
                        await browser.close(); return "skipped"

                    log_msg("⏳ Checking Captcha...")
                    start_time = time.time()
                    while BOT_RUNNING:
                        if time.time() - start_time > 60:
                            log_msg("⏰ Timeout"); await browser.close(); return "retry"

                        if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                            log_msg("🧩 CAPTCHA FOUND!")
                            await asyncio.sleep(5) 
                            
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=log_msg)
                            
                            if not ai_success: await browser.close(); return "retry"
                            
                            await asyncio.sleep(5)
                            
                            if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                                log_msg("✅ SUCCESS!")
                                await capture_step(page, "Success", wait_time=1)
                                await browser.close(); return "success"
                            else:
                                log_msg("🔁 Retry Captcha..."); await asyncio.sleep(2); continue
                        else:
                            await asyncio.sleep(1)
                
                await browser.close(); return "retry"

            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}"); await browser.close(); return "retry"
                
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}"); return "retry"