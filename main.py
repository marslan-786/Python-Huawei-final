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

# --- 🔥 SMART PROXY PARSER (FIXED) 🔥 ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    
    # CASE 1: IP:PORT:USER:PASS (Your specific format)
    # Example: 142.111.48.253:7030:tclohvzf:4evsgt1ovb3k
    if p.count(":") == 3 and "://" not in p:
        parts = p.split(":")
        return {
            "server": f"http://{parts[0]}:{parts[1]}",
            "username": parts[2],
            "password": parts[3]
        }
    
    # CASE 2: IP:PORT (Simple)
    if p.count(":") == 1 and "://" not in p:
        return {"server": f"http://{p}"}

    # CASE 3: Standard URL (http://user:pass@ip:port)
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
    # 1. Manual Proxy
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"].strip()) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    
    # 2. File Proxy (Rotate)
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                raw_proxy = random.choice(lines)
                return parse_proxy_string(raw_proxy)
        except: pass
    
    return None

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f:
            lines = f.read().splitlines()
        for num in lines:
            if num.strip(): return num.strip()
    return None

# --- API ENDPOINTS ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)
    images = [f"/captures/{os.path.basename(f)}" for f in files[:10]]
    
    prox = get_strict_proxy()
    p_disp = prox['server'] if prox else "❌ NO PROXY SET"
    
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

# --- HELPER FUNCTIONS ---
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

# --- 🔥 THE 5 CLICK STRATEGIES 🔥 ---
async def execute_click_strategy(page, element, attempt_num, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if not box: return False
        
        cx = box['x'] + box['width'] / 2
        cy = box['y'] + box['height'] / 2
        rx = box['x'] + box['width'] - 40
        ry = cy

        if attempt_num == 1:
            log_msg(f"🔹 Strategy 1 (Standard): {desc}")
            await element.click(force=True, timeout=2000)
            
        elif attempt_num == 2:
            log_msg(f"🔹 Strategy 2 (JS Force): {desc}")
            await element.evaluate("e => e.click()")
            
        elif attempt_num == 3:
            log_msg(f"🔹 Strategy 3 (Hard Tap Center): {desc}")
            await show_red_dot(page, cx, cy)
            await page.touchscreen.tap(cx, cy)
            
        elif attempt_num == 4:
            log_msg(f"🔹 Strategy 4 (Hard Tap Right): {desc}")
            await show_red_dot(page, rx, ry)
            await page.touchscreen.tap(rx, ry)
            
        elif attempt_num == 5:
            log_msg(f"🔹 Strategy 5 (CDP Raw Hammer): {desc}")
            await show_red_dot(page, cx, cy)
            client = await page.context.new_cdp_session(page)
            await client.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx, "y": cy}]})
            await asyncio.sleep(0.15)
            await client.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            
        return True
    except Exception: pass
    return False

async def secure_step(page, current_finder, next_finder_check, step_name, pre_action=None):
    for i in range(1, 6):
        if not BOT_RUNNING: return False
        try:
            if await next_finder_check().count() > 0: return True
        except: pass
        
        try:
            btn = current_finder()
            if await btn.count() > 0:
                if i > 1: log_msg(f"♻️ {step_name}: Trying Method {i}/5...")
                if pre_action: await pre_action()
                await execute_click_strategy(page, btn.first, i, step_name)
                await asyncio.sleep(0.5) 
                await capture_step(page, f"{step_name}_Try{i}", wait_time=0)
                await asyncio.sleep(3) 
            else:
                log_msg(f"⏳ Searching {step_name}...")
                await asyncio.sleep(2)
        except Exception: pass
    
    log_msg(f"❌ Failed: {step_name}")
    await capture_step(page, f"Stuck_{step_name}", wait_time=0)
    return False

# --- CORE LOGIC ---
async def master_loop():
    global BOT_RUNNING
    
    check_proxy = get_strict_proxy()
    if not check_proxy:
        log_msg("⛔ FATAL: No Proxy Set! Stopping.")
        BOT_RUNNING = False
        return

    log_msg("🟢 Worker Started.")
    
    while BOT_RUNNING:
        current_number = get_next_number()
        if not current_number:
            log_msg("ℹ️ Number list empty.")
            current_number = f"9{random.randint(100000000, 999999999)}"
            
        target_country = SETTINGS["country"]
        proxy_cfg = get_strict_proxy()
        
        if not proxy_cfg:
            log_msg("⛔ Proxy List Invalid. Stopping.")
            BOT_RUNNING = False
            break
            
        p_log = proxy_cfg['server']
        log_msg(f"🔵 Processing: {current_number} | Proxy: {p_log}")
        
        try:
            result = await run_single_session(current_number, target_country, proxy_cfg)
            if result == "success": log_msg("🎉 Verified! Next...")
            else: log_msg("❌ Failed. Next...")
        except Exception as e:
            log_msg(f"🔥 Crash: {e}")
        
        await asyncio.sleep(2)

async def run_single_session(phone_number, country_name, proxy_config):
    try:
        async with async_playwright() as p:
            launch_args = {"headless": True, "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"]}
            launch_args["proxy"] = proxy_config # STRICT PROXY INJECTION

            log_msg("🚀 Launching Browser...")
            try:
                browser = await p.chromium.launch(**launch_args)
            except Exception as e:
                log_msg(f"❌ Proxy Connection Failed: {e}")
                return "retry"

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

                # 1. REGISTER (Pure Text Match)
                if not await secure_step(
                    page, 
                    lambda: page.get_by_text("Register", exact=True), 
                    lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), 
                    "Register"
                ): await browser.close(); return "retry"

                # 2. AGREE
                cb_text = page.get_by_text("stay informed", exact=False).first
                async def click_checkbox():
                    if await cb_text.count() > 0: 
                        await execute_click_strategy(page, cb_text, 3, "Checkbox")

                if not await secure_step(
                    page,
                    lambda: page.get_by_role("button", name="Agree").or_(page.get_by_role("button", name="Next")),
                    lambda: page.get_by_text("Next", exact=True), 
                    "Agree_Btn",
                    pre_action=click_checkbox
                ): await browser.close(); return "retry"

                # 3. DOB -> PHONE
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                if not await secure_step(
                    page,
                    lambda: page.get_by_text("Next", exact=True),
                    lambda: page.get_by_text("Use phone number", exact=False),
                    "DOB_Next"
                ): await browser.close(); return "retry"

                # 4. Use Phone -> Country
                if not await secure_step(
                    page,
                    lambda: page.get_by_text("Use phone number", exact=False),
                    lambda: page.get_by_text("Country/Region"), 
                    "UsePhone"
                ): await browser.close(); return "retry"

                # 5. COUNTRY SWITCH
                log_msg(f"🌍 Selecting {country_name}...")
                list_opened = False
                
                if await page.get_by_placeholder("Search", exact=False).count() > 0:
                    list_opened = True
                else:
                    lbl = page.get_by_text("Country/Region").first
                    if await lbl.count() > 0:
                        # Try all strategies on the Label/Row
                        for i in range(1, 6):
                            await execute_click_strategy(page, lbl, 4, "CountryRow_Open") # Force Right Tap
                            await asyncio.sleep(1)
                            if await page.get_by_placeholder("Search", exact=False).count() > 0:
                                list_opened = True; break
                    else:
                        await page.touchscreen.tap(380, 200) # Blind tap
                        await asyncio.sleep(2)
                        if await page.get_by_placeholder("Search", exact=False).count() > 0: list_opened = True

                if not list_opened:
                    log_msg("❌ List Open Failed")
                    await browser.close(); return "retry"
                
                search = page.get_by_placeholder("Search", exact=False).first
                await search.click()
                await page.keyboard.type(country_name, delay=50)
                await capture_step(page, "04_Typed", wait_time=2) 
                
                matches = page.get_by_text(country_name, exact=False)
                if await matches.count() > 1: await execute_click_strategy(page, matches.nth(1), 1, "Result")
                elif await matches.count() == 1: await execute_click_strategy(page, matches.first, 1, "Result")
                else: log_msg(f"❌ Country Not Found"); await browser.close(); return "retry"
                await capture_step(page, "05_Selected", wait_time=1)

                # 6. INPUT NUMBER
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0: inp = page.locator("input").first
                
                if await inp.count() > 0:
                    log_msg("🔢 Inputting Phone...")
                    await inp.click()
                    for c in phone_number:
                        if not BOT_RUNNING: return "stopped"
                        await page.keyboard.type(c); await asyncio.sleep(0.05)
                    await page.touchscreen.tap(350, 100)
                    
                    get_code_btn = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                    await execute_click_strategy(page, get_code_btn, 1, "GET CODE")
                    await capture_step(page, "GetCodeClick", wait_time=2)

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
                            await capture_step(page, "CaptchaFound", wait_time=0)
                            
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=log_msg)
                            
                            if not ai_success: await browser.close(); return "retry"
                            
                            await asyncio.sleep(5)
                            await capture_step(page, "ResultCheck", wait_time=0)
                            
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