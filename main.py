import os
import asyncio
import random
import time
import shutil
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

app = FastAPI()

if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

try:
    from captcha_solver import solve_captcha
except ImportError:
    print("❌ ERROR: captcha_solver.py not found!")
    async def solve_captcha(page, session_id): return False

SETTINGS = {
    "country": "Russia",
    "proxy_manual": "",
}

BOT_RUNNING = False
NUMBER_QUEUE = asyncio.Queue()
logs = []

def log_msg(message):
    entry = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 200: logs.pop()

def get_proxy():
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        p = SETTINGS["proxy_manual"].strip()
        if "://" not in p: p = f"http://{p}"
        try:
            u = urlparse(p)
            return {"server": f"{u.scheme}://{u.hostname}:{u.port}", "username": u.username, "password": u.password}
        except: return None
    
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                p = random.choice(lines).strip()
                if "://" not in p: p = f"http://{p}"
                u = urlparse(p)
                return {"server": f"{u.scheme}://{u.hostname}:{u.port}", "username": u.username, "password": u.password}
        except: pass
    return None

# --- API ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    # 🔥 FIXED: Safe File Reading (No More 500 Errors)
    images = []
    try:
        if os.path.exists(CAPTURE_DIR):
            all_files = os.listdir(CAPTURE_DIR)
            valid_files = []
            for f in all_files:
                if f.endswith(".jpg"):
                    full_path = os.path.join(CAPTURE_DIR, f)
                    if os.path.exists(full_path): # Double check existence
                        valid_files.append((f, os.path.getmtime(full_path)))
            
            # Sort by new
            valid_files.sort(key=lambda x: x[1], reverse=True)
            images = [f"/captures/{f[0]}" for f in valid_files[:5]]
    except Exception: 
        pass # Ignore errors silently
        
    return JSONResponse({"logs": logs, "images": images, "running": BOT_RUNNING, "current_country": SETTINGS["country"]})

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
    log_msg(f"📂 Numbers Uploaded")
    return {"status": "saved"}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING:
        BOT_RUNNING = True
        if os.path.exists(NUMBERS_FILE):
            with open(NUMBERS_FILE, "r") as f: nums = [l.strip() for l in f.readlines() if l.strip()]
            
            while not NUMBER_QUEUE.empty(): NUMBER_QUEUE.get_nowait()
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            
            log_msg(f"🚀 Loaded {len(nums)} Numbers. Starting...")
            bt.add_task(master_loop)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- 🔥 HELPER: VISUAL TAP 🔥 ---
async def visual_tap(page, element, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            
            log_msg(f"👆 Tapping {desc}...")
            await page.touchscreen.tap(x, y)
            return True
    except: pass
    return False

# --- 🔥 HELPER: SECURE STEP 🔥 ---
async def secure_step(page, current_finder, next_finder_check, step_name, pre_action=None):
    max_retries = 5
    for i in range(max_retries):
        if not BOT_RUNNING: return False
        
        # 1. Check Success
        try:
            if await next_finder_check().count() > 0: return True
        except: pass
        
        # 2. Try Action
        try:
            btn = current_finder()
            if await btn.count() > 0:
                if i > 0: log_msg(f"♻️ Retry {i+1}: {step_name}...")
                
                if pre_action: 
                    await pre_action()
                    await asyncio.sleep(0.5)
                
                await visual_tap(page, btn.first, step_name)
                await asyncio.sleep(3) 
            else:
                if i == 0: log_msg(f"⏳ Finding {step_name}...")
                await asyncio.sleep(2)
        except Exception as e: pass
    
    # Final Fail
    log_msg(f"❌ Stuck at {step_name}")
    ts = time.strftime("%H%M%S")
    try: await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{step_name}_{ts}.jpg")
    except: pass
    return False

# --- CORE LOOP ---
async def master_loop():
    global BOT_RUNNING  # 🔥 THIS WAS THE MISSING LINE FIXING THE CRASH 🔥
    
    log_msg("🟢 DEBUG: Worker Loop Started") 
    
    while BOT_RUNNING:
        try:
            phone_number = NUMBER_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            log_msg("✅ All Done.")
            BOT_RUNNING = False
            break
        
        log_msg(f"🔵 Processing: {phone_number}")
        await run_single_session(phone_number)
        await asyncio.sleep(1)

async def run_single_session(phone_number):
    target_country = SETTINGS["country"]
    proxy_config = get_proxy()
    
    try:
        log_msg("🚀 DEBUG: Launching Browser...")
        async with async_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            }
            if proxy_config: launch_args["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_args)
            log_msg("✅ DEBUG: Browser Opened!")
            
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36",
                has_touch=True
            )
            page = await context.new_page()

            # 1. Load
            try:
                log_msg("🌐 Loading Website...")
                await page.goto(BASE_URL, timeout=60000)
            except:
                log_msg(f"⚠️ Load Failed: {phone_number}")
                await browser.close(); return

            # 2. Register -> Agree
            success = await secure_step(
                page, 
                lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")),
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                "Register"
            )
            if not success: await browser.close(); return

            # 3. Agree Page (With Checkbox)
            cb_text = page.get_by_text("stay informed", exact=False).first
            
            async def click_checkbox():
                if await cb_text.count() > 0:
                    await visual_tap(page, cb_text, "Checkbox")

            success = await secure_step(
                page,
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                lambda: page.get_by_text("Next", exact=True), # Target: DOB Next
                "Agree_Btn",
                pre_action=click_checkbox
            )
            if not success: await browser.close(); return

            # 4. DOB -> Phone
            success = await secure_step(
                page,
                lambda: page.get_by_text("Next", exact=True),
                lambda: page.get_by_text("Use phone number", exact=False),
                "DOB_Next"
            )
            if not success: await browser.close(); return

            # 5. Phone -> Country
            success = await secure_step(
                page,
                lambda: page.get_by_text("Use phone number", exact=False),
                lambda: page.get_by_text("Country/Region"), 
                "UsePhone"
            )
            if not success: await browser.close(); return

            # 6. Country Switch
            log_msg("👆 Selecting Country...")
            list_opened = False
            for i in range(4):
                # Check for Search Input
                search_box = page.get_by_placeholder("Search", exact=False)
                if await search_box.count() > 0:
                    list_opened = True; break
                
                arrow = page.locator(".hwid-list-item-arrow").first
                label = page.get_by_text("Country/Region").first
                
                if await arrow.count() > 0: 
                    await visual_tap(page, arrow, "Arrow")
                elif await label.count() > 0: 
                    # Fallback Coords
                    await page.touchscreen.tap(370, 150)
                await asyncio.sleep(2) 
            
            if not list_opened:
                log_msg("❌ Failed to open Country List.")
                await browser.close(); return
            
            # Search & Select
            search = page.get_by_placeholder("Search", exact=False).first
            log_msg("⌨️ Typing Country...")
            await visual_tap(page, search, "Search")
            await page.keyboard.type(target_country, delay=50)
            await asyncio.sleep(2) 
            
            matches = page.get_by_text(target_country, exact=False)
            count = await matches.count()
            if count > 1: await visual_tap(page, matches.nth(1), "CountryResult")
            elif count == 1: await visual_tap(page, matches.first, "CountryResult")
            else: 
                log_msg(f"❌ Country not found")
                await browser.close(); return
            await asyncio.sleep(2)

            # 7. Input Number
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: inp = page.locator("input").first
            
            if await inp.count() > 0:
                log_msg("⌨️ Typing Phone...")
                await visual_tap(page, inp, "Input")
                await page.keyboard.type(phone_number, delay=20)
                await page.touchscreen.tap(350, 100) # Hide KB
                
                get_code_btn = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                await visual_tap(page, get_code_btn, "GET CODE")
                
                # Check Error
                await asyncio.sleep(2)
                err_popup = page.get_by_text("An unexpected problem", exact=False)
                if await err_popup.count() > 0:
                    log_msg("⛔ Not Supported")
                    ts = time.strftime("%H%M%S")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Popup_{phone_number}_{ts}.jpg")
                    await browser.close(); return

                # Captcha
                log_msg("⏳ Checking Captcha...")
                start_time = time.time()
                while time.time() - start_time < 15:
                    if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                        break
                    await asyncio.sleep(1)
                
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    log_msg("🧩 Solving...")
                    await asyncio.sleep(5)
                    sess_id = f"sess_{int(time.time())}"
                    
                    if await solve_captcha(page, sess_id):
                        await asyncio.sleep(5)
                        if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                            log_msg("✅ Success: Verified!")
                            ts = time.strftime("%H%M%S")
                            await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}_{ts}.jpg")
                        else:
                            log_msg("❌ Failed: Captcha Stuck")
                            ts = time.strftime("%H%M%S")
                            await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{phone_number}_{ts}.jpg")
                    else:
                        log_msg("⚠️ Solver Error")
                else:
                    log_msg("✅ Success: Direct")
                    ts = time.strftime("%H%M%S")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}_{ts}.jpg")
            
            await browser.close()

    except Exception as e:
        log_msg(f"🔥 Error: {e}")