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

# --- 🔥 USER SETTINGS ---
live_logs = True 

# --- 🔥 HARDCODED SCRAPER API ---
API_KEY = '9643e678c2fa6efe4d2c7cf7b2206be0'
SCRAPER_PROXY_URL = f"http://scraperapi.residential=true:{API_KEY}@proxy-server.scraperapi.com:8001"

# --- CONFIG ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
SUCCESS_FILE = "success.txt"
FAILED_FILE = "failed.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# File Init
for f in [NUMBERS_FILE, SUCCESS_FILE, FAILED_FILE, PROXY_FILE]:
    if not os.path.exists(f): open(f, 'w').close()

try:
    from captcha_solver import solve_captcha
except ImportError:
    async def solve_captcha(page, session_id, logger=print): return False

SETTINGS = {"country": "Russia", "proxy_manual": ""}
BOT_RUNNING = False
logs = []

# --- HELPERS ---
def log_msg(message, level="step"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

def save_data(filename, data):
    with open(filename, "a", encoding="utf-8") as f: f.write(f"{data}\n")

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: lines = f.read().splitlines()
        valid = [l.strip() for l in lines if l.strip()]
        if valid: return valid[0]
    return None

def remove_number(number):
    if not os.path.exists(NUMBERS_FILE): return
    with open(NUMBERS_FILE, "r") as f: lines = f.readlines()
    with open(NUMBERS_FILE, "w") as f:
        for line in lines:
            if line.strip() != number: f.write(line)

def count_lines(filename):
    if not os.path.exists(filename): return 0
    with open(filename, "r") as f: return len([l for l in f if l.strip()])

# --- PROXY ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if p.count(":") == 3 and "://" not in p:
        parts = p.split(":")
        return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]}
    if "://" not in p: p = f"http://{p}"
    try:
        parsed = urlparse(p)
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username: cfg["username"] = parsed.username
        if parsed.password: cfg["password"] = parsed.password
        return cfg
    except: return None

def get_strict_proxy():
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy_string(random.choice(lines))
        except: pass
    return parse_proxy_string(SCRAPER_PROXY_URL)

# --- VISUALS ---
async def capture_step(page, step_name, wait_time=0, force=False):
    if not BOT_RUNNING: return
    if not live_logs and not force: return
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

# --- CLICK LOGIC ---
async def click_element(page, finder, name):
    try:
        # User requested Text locators primarily, no button strategy
        el = finder()
        if await el.count() > 0:
            box = await el.first.bounding_box()
            if box:
                cx = box['x'] + box['width'] / 2
                cy = box['y'] + box['height'] / 2
                log_msg(f"🖱️ Clicking {name}...", level="step")
                await show_red_dot(page, cx, cy)
                await page.touchscreen.tap(cx, cy) # Using Tap as it's mobile view
                return True
        return False
    except: return False

# 🔥 SMART ACTION LOGIC (Wait & Retry) 🔥
async def smart_action(page, finder, verifier, step_name, wait_after=3):
    if not BOT_RUNNING: return False
    
    # 1. Initial Wait
    try:
        log_msg(f"🔍 Looking for {step_name}...", level="step")
        await page.wait_for_selector("body", timeout=5000)
    except: pass

    # 2. Try Clicking
    for attempt in range(1, 4): # 3 Attempts
        if not BOT_RUNNING: return False
        
        # Already verified?
        if verifier and await verifier().count() > 0:
            log_msg(f"✅ {step_name} Already Done.", level="step")
            return True

        # Click
        clicked = await click_element(page, finder, f"{step_name} (Try {attempt})")
        
        if clicked:
            log_msg(f"⏳ Waiting {wait_after}s...", level="step")
            await asyncio.sleep(wait_after)
            
            # 3. VERIFY
            if verifier and await verifier().count() > 0:
                log_msg(f"✅ {step_name} Success!", level="step")
                await capture_step(page, f"Post_{step_name}")
                return True
            
            # Check if button still there
            elif await finder().count() > 0:
                log_msg(f"⚠️ {step_name} click failed (Button visible). Retrying...", level="step")
                await capture_step(page, f"Fail_{step_name}")
                continue 
            
            # Loading check
            else:
                log_msg(f"⏳ Elements gone (Loading?)... Waiting 5s...", level="step")
                await asyncio.sleep(5)
                if verifier and await verifier().count() > 0:
                    log_msg(f"✅ {step_name} Success (After Load)!", level="step")
                    return True
                else:
                    log_msg(f"⚠️ Stuck / Loading...", level="step")
                    await capture_step(page, f"Stuck_{step_name}")
        else:
            log_msg(f"❌ {step_name} Not Found (Attempt {attempt})", level="step")
            await asyncio.sleep(2)

    return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    if not get_strict_proxy():
        log_msg("⛔ FATAL: No Proxy!", level="main"); BOT_RUNNING = False; return

    log_msg("🟢 Worker Started.", level="main")
    
    while BOT_RUNNING:
        current_number = get_next_number()
        if not current_number:
            log_msg("ℹ️ No Numbers.", level="main"); BOT_RUNNING = False; break
            
        proxy_cfg = get_strict_proxy()
        log_msg(f"🔵 Processing: {current_number}", level="main") 
        
        try:
            res = await run_session(current_number, SETTINGS["country"], proxy_cfg)
            if res == "success":
                log_msg("🎉 Verified!", level="main")
                save_data(SUCCESS_FILE, current_number)
                remove_number(current_number)
            elif res == "failed":
                log_msg("❌ Failed (Hard Skip).", level="main")
                save_data(FAILED_FILE, current_number)
                remove_number(current_number)
            
        except Exception as e:
            log_msg(f"🔥 Crash: {e}", level="main")
        
        await asyncio.sleep(2)

async def run_session(phone, country, proxy):
    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": True, 
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--ignore-certificate-errors", "--disable-web-security"]
            }
            launch_args["proxy"] = proxy 

            log_msg("🚀 Launching...", level="step")
            try: browser = await p.chromium.launch(**launch_args)
            except Exception as e: log_msg(f"❌ Proxy Fail: {e}", level="main"); return "retry"

            pixel_5 = p.devices['Pixel 5'].copy()
            pixel_5['viewport'] = {'width': 412, 'height': 950}
            pixel_5['has_touch'] = True 
            
            context = await browser.new_context(**pixel_5, locale="en-US", ignore_https_errors=True)
            page = await context.new_page()

            # --- STEP 1: LOAD URL ---
            log_msg("🌐 Opening URL...", level="step")
            try:
                if not BOT_RUNNING: return "stopped"
                await page.goto(BASE_URL, timeout=90000)
                log_msg("⏳ Page Load Wait (5s)...", level="step")
                await asyncio.sleep(5) 
                await capture_step(page, "01_Loaded")
            except: return "retry"

            # --- STEP 2: REGISTER (By Text) ---
            if not await smart_action(
                page, 
                lambda: page.get_by_text("Register", exact=False), # Finder: Text "Register"
                lambda: page.get_by_text("Agree", exact=False).or_(page.get_by_text("Stay informed", exact=False)), # Verifier
                "Register_Text",
                wait_after=3
            ): return "retry"

            # --- STEP 3: AGREE (Text Logic) ---
            # 1. Stay Informed (Text)
            cb = page.get_by_text("Stay informed", exact=False)
            if await cb.count() > 0:
                log_msg("🖱️ Tapping Stay informed...", level="step")
                await cb.click()
                await asyncio.sleep(1)
            
            # 2. Agree (Last)
            # User: "Agree text ke tor pe... aur last wala agree"
            if not await smart_action(
                page,
                lambda: page.get_by_text("Agree", exact=False).last, # Finder: LAST "Agree" text
                lambda: page.get_by_text("Date of birth", exact=False),
                "Agree_Last",
                wait_after=3
            ): return "retry"

            # --- STEP 4: DOB (No Scroll, Just Next) ---
            # User: "Scroll khatam... sirf next kare... last wala next"
            if not await smart_action(
                page,
                lambda: page.get_by_text("Next", exact=False).last, # Finder: LAST "Next" text
                lambda: page.get_by_text("Use phone number", exact=False),
                "DOB_Next_Last",
                wait_after=3
            ): return "retry"

            # --- STEP 5: PHONE TAB (By Text) ---
            # User: "Use phone number usko bhi text ke tor pe dhundo"
            if not await smart_action(
                page,
                lambda: page.get_by_text("Use phone number", exact=False),
                lambda: page.get_by_text("Country/Region"), 
                "UsePhone_Text",
                wait_after=3
            ): return "retry"

            # --- STEP 6: COUNTRY ---
            log_msg(f"🌍 Selecting {country}...", level="step")
            
            # Open List
            if not await smart_action(
                page,
                lambda: page.get_by_text("Hong Kong", exact=False).or_(page.locator(".arrow-icon").first),
                lambda: page.get_by_placeholder("Search", exact=False),
                "Open_Country_List",
                wait_after=2
            ): return "retry"

            # Search & Select
            search = page.get_by_placeholder("Search", exact=False).first
            await search.click()
            await page.keyboard.type(country, delay=50)
            await asyncio.sleep(2)
            await capture_step(page, "04_Country_Typed")
            
            matches = page.get_by_text(country, exact=False)
            if await matches.count() > 0:
                await matches.first.click()
                await asyncio.sleep(3) # 3s wait
            else:
                log_msg("❌ Country Not Found", level="main"); await browser.close(); return "retry"

            # --- STEP 7: INPUT PHONE ---
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: inp = page.locator("input").first
            
            if await inp.count() > 0:
                log_msg("🔢 Inputting Phone...", level="step")
                await inp.click()
                for c in phone:
                    if not BOT_RUNNING: return "stopped"
                    await page.keyboard.type(c); await asyncio.sleep(0.05)
                await page.touchscreen.tap(350, 100) # Close keyboard
                await capture_step(page, "05_Filled")
                
                # --- STEP 8: GET CODE (CRITICAL WAIT) ---
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code"))
                if await get_code.count() > 0:
                    await get_code.first.click()
                    
                    # 🔥 HARD WAIT 10 SECONDS 🔥
                    log_msg("⏳ Hard Wait: 10s for Captcha...", level="main")
                    await capture_step(page, "06_Clicked_GetCode", wait_time=5) # 5s Capture
                    await asyncio.sleep(5) # Total 10s
                    await capture_step(page, "07_Wait_Done")

                    # CHECK STATE
                    if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                        log_msg("⛔ FATAL: System Error", level="main")
                        await capture_step(page, "Error_Popup", force=True)
                        await browser.close(); return "failed"

                    # Captcha Logic (Using previous script logic)
                    start_solve_time = time.time()
                    while BOT_RUNNING:
                        if time.time() - start_solve_time > 120: break

                        # 1. CAPTCHA FOUND
                        if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                            log_msg("🧩 CAPTCHA FOUND!", level="main")
                            await capture_step(page, "08_Captcha_Found", force=True)
                            
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=lambda m: log_msg(m, level="step"))
                            
                            if not ai_success:
                                log_msg("⚠️ Solver Failed", level="step")
                                await browser.close(); return "retry"
                            
                            await asyncio.sleep(5)
                            
                            # Re-check
                            if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                                log_msg("✅ CAPTCHA SOLVED!", level="main")
                                await capture_step(page, "Success_Solved", force=True)
                                await browser.close(); return "success"
                            else:
                                log_msg("🔁 Captcha still there...", level="main")
                                continue
                        
                        # 2. SUCCESS (DIRECT)
                        if await page.get_by_text("sent", exact=False).count() > 0:
                            log_msg("✅ CODE SENT (Direct)!", level="main")
                            await capture_step(page, "Success_Direct", force=True)
                            await browser.close(); return "success"
                        
                        # 3. NOTHING (HARD SKIP)
                        log_msg("❌ No Captcha & No Success.", level="main")
                        await capture_step(page, "Error_Nothing", force=True)
                        await browser.close(); return "failed"

                else:
                    log_msg("❌ Get Code Missing", level="step")
                    return "retry"

            await browser.close(); return "retry"

    except Exception as e:
        log_msg(f"❌ Error: {str(e)}", level="main")
        return "retry"
    except: return "retry"

# --- API ENDPOINTS (RESTORED) ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)[:10]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    p_check = get_strict_proxy()
    p_disp = p_check['server'] if p_check else "❌ No Proxy"
    stats = {
        "remaining": count_lines(NUMBERS_FILE),
        "success": count_lines(SUCCESS_FILE),
        "failed": count_lines(FAILED_FILE)
    }
    return JSONResponse({
        "logs": logs[:50], 
        "images": images, 
        "running": BOT_RUNNING, 
        "current_country": SETTINGS["country"], 
        "current_proxy": p_disp,
        "stats": stats
    })

@app.get("/download/{ftype}")
async def download_file(ftype: str):
    fname = f"{ftype}.txt"
    if os.path.exists(fname): return FileResponse(fname, filename=fname)
    return {"error": "File not found"}

@app.post("/clear_data")
async def clear_data():
    global logs
    logs = []
    open(NUMBERS_FILE, 'w').close()
    open(SUCCESS_FILE, 'w').close()
    open(FAILED_FILE, 'w').close()
    for f in glob.glob(f'{CAPTURE_DIR}/*'): os.remove(f)
    return {"status": "cleared"}

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
    log_msg("🛑 STOP COMMAND RECEIVED.", level="main")
    return {"status": "stopping"}