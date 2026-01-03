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
VIDEO_PATH = f"{CAPTURE_DIR}/proof.mp4"
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

# --- SETUP DIRECTORIES ---
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

# --- GLOBAL STATE ---
BOT_RUNNING = False
logs = []

def log_msg(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

# --- PROXY PARSER ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if "://" not in p: p = f"http://{p}"
    try:
        parsed = urlparse(p)
        proxy_config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username and parsed.password:
            proxy_config["username"] = parsed.username
            proxy_config["password"] = parsed.password
        return proxy_config
    except Exception as e:
        log_msg(f"⚠️ Proxy Parse Error: {e}")
        return None

# --- PROXY HELPER (STRICT & ROTATING) ---
def get_next_proxy():
    # 1. Manual Proxy (Priority)
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"].strip()) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"]), "Manual"
    
    # 2. Proxy List (Rotation)
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                selected = random.choice(lines)
                return parse_proxy_string(selected), "Rotated"
        except: pass
    
    # 3. Direct (Only if no proxy settings exist)
    return None, "Direct"

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f:
            lines = f.read().splitlines()
        for num in lines:
            if num.strip(): return num.strip()
    # Random fallback if file empty
    prefix = "9"
    rest = ''.join([str(random.randint(0, 9)) for _ in range(9)])
    return f"{prefix}{rest}"

# --- API ENDPOINTS ---

@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)
    images = [f"/captures/{os.path.basename(f)}" for f in files[:10]] # Limit to last 10 images to save bandwidth
    
    return JSONResponse({
        "logs": logs[:50], 
        "images": images,
        "running": BOT_RUNNING,
        "current_country": SETTINGS["country"]
    })

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country
    SETTINGS["proxy_manual"] = manual_proxy
    log_msg(f"⚙️ Settings Saved: Country={country}")
    return {"status": "updated"}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Proxy List Uploaded")
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

# --- VISUAL HELPERS ---

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
            dot.style.left = '{x-15}px'; 
            dot.style.top = '{y-15}px';
            dot.style.width = '30px'; 
            dot.style.height = '30px'; 
            dot.style.background = 'rgba(255, 0, 0, 0.9)'; 
            dot.style.borderRadius = '50%'; 
            dot.style.zIndex = '2147483647'; 
            dot.style.pointerEvents = 'none'; 
            dot.style.border = '3px solid white'; 
            dot.style.boxShadow = '0 0 15px rgba(255,0,0,0.8)';
            document.body.appendChild(dot);
            setTimeout(() => {{ dot.remove(); }}, 3000);
        """)
    except: pass

async def visual_tap(page, element, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            
            await show_red_dot(page, x, y)
            log_msg(f"👆 Tapping {desc}...")
            await page.touchscreen.tap(x, y)
            return True
    except: pass
    return False

async def secure_step(page, current_finder, next_finder_check, step_name, pre_action=None):
    max_retries = 5
    for i in range(max_retries):
        if not BOT_RUNNING: return False
        try:
            if await next_finder_check().count() > 0: return True
        except: pass
        try:
            btn = current_finder()
            if await btn.count() > 0:
                if i > 0: log_msg(f"♻️ Retry {i+1}: {step_name}...")
                
                if pre_action: await pre_action()
                await visual_tap(page, btn.first, step_name)
                await asyncio.sleep(0.5) 
                await capture_step(page, f"{step_name}_Click_{i+1}", wait_time=0)
                await asyncio.sleep(3) 
            else:
                log_msg(f"⏳ Finding {step_name}...")
                await asyncio.sleep(2)
        except Exception as e: log_msg(f"⚠️ Error: {e}")
    
    log_msg(f"❌ Failed to pass {step_name}.")
    return False

# --- MAIN LOOP ---
async def master_loop():
    global BOT_RUNNING
    
    while BOT_RUNNING:
        current_number = get_next_number()
        target_country = SETTINGS["country"]
        
        # 🔥 GET NEW PROXY PER NUMBER 🔥
        proxy_cfg, proxy_type = get_next_proxy()
        
        # Display safe proxy string
        p_display = proxy_cfg['server'] if proxy_cfg else "Direct"
        log_msg(f"🔵 Processing: {current_number} | Proxy: {p_display}")
        
        success = False
        # Single Attempt per number (Keep it simple)
        try:
            result = await run_single_session(current_number, target_country, proxy_cfg)
            if result == "success": success = True
        except Exception as e:
            log_msg(f"🔥 Crash Error: {e}")
        
        if success: log_msg("🎉 Verified! Moving to next...")
        else: log_msg("❌ Failed. Checking next number...")
        
        await asyncio.sleep(2)

async def run_single_session(phone_number, country_name, proxy_config):
    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            }
            if proxy_config: launch_args["proxy"] = proxy_config

            log_msg("🚀 Launching Browser...")
            browser = await p.chromium.launch(**launch_args)
            
            pixel_5 = p.devices['Pixel 5'].copy()
            pixel_5['user_agent'] = "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            pixel_5['viewport'] = {'width': 412, 'height': 950} 
            
            context = await browser.new_context(**pixel_5, locale="en-US")
            page = await context.new_page()

            log_msg("🌐 Loading Website...")
            try:
                if not BOT_RUNNING: return "stopped"
                await page.goto(BASE_URL, timeout=60000)
                await capture_step(page, "01_Loaded", wait_time=2)

                # STEP 1: REGISTER -> AGREE
                success = await secure_step(
                    page, 
                    lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")),
                    lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                    "Register"
                )
                if not success: await browser.close(); return "retry"

                # STEP 2: AGREE PAGE
                cb_text = page.get_by_text("stay informed", exact=False).first
                try: await cb_text.wait_for(timeout=10000)
                except: pass
                await capture_step(page, "02_AgreePage", wait_time=0.5)

                async def click_checkbox():
                    if await cb_text.count() > 0:
                        await visual_tap(page, cb_text, "Checkbox")
                        await capture_step(page, "02_Checkbox", wait_time=0.5)

                success = await secure_step(
                    page,
                    lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                    lambda: page.get_by_text("Next", exact=True), # Target: DOB Next
                    "Agree_Btn",
                    pre_action=click_checkbox
                )
                if not success: await browser.close(); return "retry"

                # STEP 3: DOB -> PHONE
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                success = await secure_step(
                    page,
                    lambda: page.get_by_text("Next", exact=True),
                    lambda: page.get_by_text("Use phone number", exact=False),
                    "DOB_Next"
                )
                if not success: await browser.close(); return "retry"

                # STEP 4: PHONE -> COUNTRY
                success = await secure_step(
                    page,
                    lambda: page.get_by_text("Use phone number", exact=False),
                    lambda: page.get_by_text("Country/Region"), 
                    "UsePhone"
                )
                if not success: await browser.close(); return "retry"

                # STEP 5: COUNTRY SWITCH
                log_msg(f"🌍 Selecting: {country_name}...")
                list_opened = False
                for i in range(4):
                    search_box = page.get_by_placeholder("Search", exact=False)
                    if await search_box.count() > 0:
                        list_opened = True; break
                    
                    arrow = page.locator(".hwid-list-item-arrow").first
                    label = page.get_by_text("Country/Region").first
                    
                    if await arrow.count() > 0: 
                        await visual_tap(page, arrow, "Arrow")
                    elif await label.count() > 0: 
                        log_msg("⚠️ Using Coords...")
                        await page.touchscreen.tap(370, 150)
                    else:
                        log_msg("❌ Country Row Not Found")
                    await asyncio.sleep(2) 
                
                if not list_opened:
                    log_msg("❌ Failed to open List.")
                    await browser.close(); return "retry"
                
                await capture_step(page, "03_ListOpen", wait_time=0.5)
                search = page.get_by_placeholder("Search", exact=False).first
                await visual_tap(page, search, "Search")
                await page.keyboard.type(country_name, delay=50)
                await capture_step(page, "04_Typed", wait_time=2) 
                
                matches = page.get_by_text(country_name, exact=False)
                count = await matches.count()
                if count > 1: await visual_tap(page, matches.nth(1), f"Result_{country_name}")
                elif count == 1: await visual_tap(page, matches.first, f"Result_{country_name}")
                else: log_msg(f"❌ {country_name} not found"); await browser.close(); return "retry"
                await capture_step(page, "05_Selected", wait_time=1)

                # STEP 6: INPUT NUMBER
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0: inp = page.locator("input").first
                
                if await inp.count() > 0:
                    log_msg("🔢 Inputting Number...")
                    await visual_tap(page, inp, "Input")
                    await page.keyboard.type(phone_number, delay=20)
                    await page.touchscreen.tap(350, 100)
                    await capture_step(page, "06_NumTyped", wait_time=0.5)
                    
                    get_code_btn = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                    if await get_code_btn.count() > 0:
                        await visual_tap(page, get_code_btn, "GET CODE")
                        await capture_step(page, "07_GetCode", wait_time=2)
                        
                        # Error Check
                        err_popup = page.get_by_text("An unexpected problem", exact=False)
                        if await err_popup.count() > 0:
                            log_msg("⛔ Not Supported")
                            await capture_step(page, "Error_Popup", wait_time=0)
                            await browser.close()
                            return "skipped"

                        log_msg("⏳ Checking Captcha...")
                        start_time = time.time()
                        
                        while BOT_RUNNING:
                            if time.time() - start_time > 60:
                                log_msg("⏰ Timeout"); await browser.close(); return "retry"

                            if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                log_msg("🧩 CAPTCHA FOUND!")
                                await asyncio.sleep(5) 
                                await capture_step(page, "08_Captcha", wait_time=0)
                                
                                session_id = f"sess_{int(time.time())}"
                                ai_success = await solve_captcha(page, session_id, logger=log_msg)
                                
                                if not ai_success: await browser.close(); return "retry"
                                
                                await capture_step(page, "09_Solving", wait_time=5)
                                
                                if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                                    log_msg("✅ SUCCESS!")
                                    await capture_step(page, "10_Success", wait_time=1)
                                    await browser.close(); return "success"
                                else:
                                    log_msg("🔁 Failed. Retrying..."); await asyncio.sleep(2); continue
                            else:
                                await asyncio.sleep(1)
                
                await browser.close(); return "retry"

            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}"); await browser.close(); return "retry"
                
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}"); return "retry"