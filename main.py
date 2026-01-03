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
    "use_proxy_file": False
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
    if not proxy_str: return None
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

# --- PROXY HELPER ---
def get_current_proxy():
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"].strip()) > 3:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                return parse_proxy_string(random.choice(lines))
        except: pass
    return None

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f:
            lines = f.read().splitlines()
        for num in lines:
            if num.strip(): return num.strip()
    prefix = "9"
    rest = ''.join([str(random.randint(0, 9)) for _ in range(9)])
    return f"{prefix}{rest}"

# --- API ENDPOINTS ---

@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    p_log = "NO"
    curr_prox = get_current_proxy()
    if curr_prox: p_log = curr_prox['server']
    
    return JSONResponse({
        "logs": logs[:50], 
        "images": images,
        "running": BOT_RUNNING,
        "current_country": SETTINGS["country"],
        "current_proxy": p_log
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
    log_msg(f"📂 Proxy List Uploaded: {file.filename}")
    return {"status": "saved"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Numbers File Uploaded: {file.filename}")
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

@app.post("/generate_video")
async def trigger_video():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'))
    if not files: return {"status": "error", "error": "No images"}
    try:
        with imageio.get_writer(VIDEO_PATH, fps=2, format='FFMPEG', quality=8) as writer:
            for filename in files:
                try: writer.append_data(imageio.imread(filename))
                except: continue
        return {"status": "done"}
    except Exception as e: return {"status": "error", "error": str(e)}

# --- HELPER FUNCTIONS ---

async def capture_step(page, step_name, wait_time=0):
    if not BOT_RUNNING: return
    if wait_time > 0: await asyncio.sleep(wait_time)
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{CAPTURE_DIR}/{timestamp}_{step_name}.jpg"
    try: await page.screenshot(path=filename)
    except: pass

async def visual_tap(page, element, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            await page.evaluate(f"""
                var dot = document.createElement('div');
                dot.style.position = 'absolute'; left = '{x}px'; top = '{y}px';
                dot.style.width = '15px'; dot.style.height = '15px'; dot.style.background = 'rgba(255,0,0,0.6)';
                dot.style.borderRadius = '50%'; dot.style.zIndex = '999999'; dot.style.pointerEvents='none';
                document.body.appendChild(dot);
            """)
            log_msg(f"👆 Clicking {desc}...")
            await page.touchscreen.tap(x, y)
            return True
    except: pass
    return False

# 🔥 SMART WAIT FUNCTION (5-Second Rule) 🔥
async def smart_tap(page, finder_func, name, max_wait=5):
    """
    Waits up to 5 seconds for an element. 
    Checks every 1 second.
    If found, clicks and returns True.
    """
    if not BOT_RUNNING: return False
    
    log_msg(f"👀 Looking for: {name}...")
    
    for i in range(max_wait):
        if not BOT_RUNNING: return False
        try:
            element = finder_func() 
            if await element.count() > 0:
                await visual_tap(page, element.first, name)
                return True
        except: pass
        await asyncio.sleep(1)
        
    log_msg(f"❌ Timed out: {name} not found after {max_wait}s")
    return False

# --- CORE LOGIC LOOP ---
async def master_loop():
    
    while BOT_RUNNING:
        current_number = get_next_number()
        target_country = SETTINGS["country"]
        proxy_cfg = get_current_proxy()
        
        p_log = "NO"
        if proxy_cfg: p_log = f"{proxy_cfg['server']}"
        
        log_msg(f"🎬 NEW NUMBER: {current_number} | Country: {target_country} | Proxy: {p_log}")
        
        # 🔥 RETRY LOGIC: Try 3 Times per Number
        success = False
        for attempt in range(1, 4):
            if not BOT_RUNNING: break
            
            log_msg(f"🔹 Attempt {attempt}/3 for {current_number}")
            try:
                result = await run_single_session(current_number, target_country, proxy_cfg)
            except Exception as e:
                log_msg(f"🔥 Crash Error: {e}")
                result = "retry"
            
            if result == "success":
                success = True
                break 
            else:
                log_msg("⚠️ Attempt Failed. Retrying same number...")
                await asyncio.sleep(2)
        
        if success:
            log_msg("🎉 Verified! Moving to next number...")
        else:
            log_msg("❌ Failed 3 times. Skipping number.")
        
        await asyncio.sleep(1)

async def run_single_session(phone_number, country_name, proxy_config):
    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            }
            if proxy_config: launch_args["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_args)
            pixel_5 = p.devices['Pixel 5'].copy()
            pixel_5['user_agent'] = "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            pixel_5['viewport'] = {'width': 412, 'height': 950} 
            
            context = await browser.new_context(**pixel_5, locale="en-US")
            page = await context.new_page()

            log_msg("🚀 Navigating...")
            try:
                if not BOT_RUNNING: return "stopped"
                await page.goto(BASE_URL, timeout=60000)
                await capture_step(page, "01_HomePage", wait_time=3)

                # --- STEP 1: REGISTER ---
                found = await smart_tap(page, lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")), "Register")
                if not found: await browser.close(); return "retry"
                await capture_step(page, "02_RegClicked", wait_time=1)

                # --- STEP 2: AGREE (WITH CHECKBOX FIX) ---
                # 🔥 FIX: Click the text next to checkbox first!
                checkbox_text = page.get_by_text("stay informed", exact=False).first
                if await checkbox_text.count() > 0:
                    await visual_tap(page, checkbox_text, "CheckboxText")
                    await asyncio.sleep(0.5)
                
                # Now click Agree
                found = await smart_tap(page, lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), "Agree")
                if not found: await browser.close(); return "retry"
                await capture_step(page, "03_AgreeClicked", wait_time=1)

                # --- STEP 3: DOB ---
                # DOB requires scrolling first
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                
                found = await smart_tap(page, lambda: page.get_by_text("Next", exact=True), "DOB_Next")
                if not found: await browser.close(); return "retry"
                await capture_step(page, "04_DOBClicked", wait_time=1)

                # --- STEP 4: USE PHONE NUMBER ---
                found = await smart_tap(page, lambda: page.get_by_text("Use phone number", exact=False), "UsePhone")
                if not found: await browser.close(); return "retry"
                await capture_step(page, "05_PhoneOptClicked", wait_time=1)

                # --- STEP 5: COUNTRY SWITCH (Last Step) ---
                log_msg(f"🌍 Now switching to {country_name}...")
                found = await smart_tap(page, lambda: page.get_by_text("Hong Kong").or_(page.get_by_text("Country/Region")), "CountrySelector")
                if not found: await browser.close(); return "retry"
                await capture_step(page, "06_ListOpened", wait_time=0.5)
                
                # Search Bar
                search = page.locator("input").first
                if await search.count() > 0:
                    await visual_tap(page, search, "Search")
                    await page.keyboard.type(country_name, delay=50)
                    await capture_step(page, "07_Typed", wait_time=3) 
                    
                    matches = page.get_by_text(country_name, exact=False)
                    if await matches.count() > 1:
                        await visual_tap(page, matches.nth(1), f"CountryResult_{country_name}")
                    elif await matches.count() == 1:
                        await visual_tap(page, matches.first, f"CountryResult_{country_name}")
                    else:
                        log_msg(f"❌ {country_name} not found"); await browser.close(); return "retry"
                    
                    await capture_step(page, "08_Selected", wait_time=1)
                else:
                    log_msg("❌ Search bar missing"); await browser.close(); return "retry"

                # --- STEP 6: INPUT NUMBER ---
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0: inp = page.locator("input").first
                
                if await inp.count() > 0:
                    await visual_tap(page, inp, "Input")
                    for c in phone_number:
                        if not BOT_RUNNING: return "stopped"
                        await page.keyboard.type(c); await asyncio.sleep(0.05)
                    await page.touchscreen.tap(350, 100) # Hide KB
                    await capture_step(page, "09_NumTyped", wait_time=0.5)
                    
                    # Get Code
                    found = await smart_tap(page, lambda: page.locator(".get-code-btn").or_(page.get_by_text("Get code")), "GetCode")
                    if not found: await browser.close(); return "retry"
                    
                    log_msg("⏳ Waiting for Captcha...")
                    start_time = time.time()
                    
                    while BOT_RUNNING:
                        if time.time() - start_time > 60:
                            log_msg("⏰ Captcha Timeout"); await browser.close(); return "retry"

                        captcha_frame = None
                        for frame in page.frames:
                            try:
                                if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                    captcha_frame = frame; break
                            except: pass
                        
                        if captcha_frame:
                            log_msg("🧩 CAPTCHA DETECTED.")
                            await capture_step(page, "11_CaptchaFound", wait_time=0.5)
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=log_msg)
                            
                            if not ai_success: await browser.close(); return "retry"
                            
                            await capture_step(page, "12_Solving", wait_time=5)
                            
                            is_still_there = False
                            for frame in page.frames:
                                try:
                                    if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                        is_still_there = True; break
                                except: pass
                            
                            if not is_still_there:
                                log_msg("✅ SUCCESS!")
                                await capture_step(page, "13_Success", wait_time=1)
                                await browser.close(); return "success"
                            else:
                                log_msg("🔁 Verification Failed. Trying again..."); await asyncio.sleep(2); continue
                        else:
                            await asyncio.sleep(1)
                
                await browser.close(); return "retry"

            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}"); await browser.close(); return "retry"
                
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}"); return "retry"