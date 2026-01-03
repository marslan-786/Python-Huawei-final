import os
import asyncio
import random
import time
import shutil
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
CONCURRENT_WORKERS = 10  # 🔥 10 Parallel Tabs

app = FastAPI()

# --- SETUP DIRS ---
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# --- IMPORT SOLVER ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    print("❌ ERROR: captcha_solver.py not found!")
    async def solve_captcha(page, session_id): return False

# --- GLOBAL SETTINGS ---
SETTINGS = {
    "country": "Russia",
    "proxy_manual": "",
}

# --- GLOBAL STATE ---
BOT_RUNNING = False
NUMBER_QUEUE = asyncio.Queue()
# Note: We keep 'logs' for the frontend terminal, but only push clean messages
logs = [] 

def log_msg(message):
    # Only keep last 50 logs for UI
    entry = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(entry) # Console
    logs.insert(0, entry)
    if len(logs) > 50: logs.pop()

# --- PROXY HELPER ---
def parse_proxy(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if "://" not in p: p = f"http://{p}"
    try:
        u = urlparse(p)
        cfg = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
        if u.username and u.password:
            cfg["username"] = u.username
            cfg["password"] = u.password
        return cfg
    except: return None

def get_proxy():
    # 1. Manual
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5: 
        return parse_proxy(SETTINGS["proxy_manual"])
    # 2. File
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy(random.choice(lines))
        except: pass
    return None

# --- API ENDPOINTS (Connected to index.html) ---

@app.get("/")
async def read_index():
    return FileResponse('index.html')

@app.get("/status")
async def get_status():
    # Frontend expects specific JSON format
    return JSONResponse({
        "logs": logs, 
        "images": [], # No images anymore
        "running": BOT_RUNNING,
        "current_country": SETTINGS["country"],
        "current_proxy": SETTINGS["proxy_manual"] if SETTINGS["proxy_manual"] else "Auto/File"
    })

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country
    SETTINGS["proxy_manual"] = manual_proxy
    log_msg(f"⚙️ Settings Updated: Country={country}")
    return {"status": "updated"}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Proxies Uploaded")
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
            with open(NUMBERS_FILE, "r") as f:
                nums = [l.strip() for l in f.readlines() if l.strip()]
            
            if not nums:
                log_msg("⚠️ Numbers file is empty!")
                BOT_RUNNING = False
                return {"status": "error"}

            # Fill Queue
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            
            log_msg(f"🚀 Starting {CONCURRENT_WORKERS} Workers for {len(nums)} numbers...")
            
            # Start Workers
            for i in range(CONCURRENT_WORKERS):
                bt.add_task(worker_loop, i)
        else:
            log_msg("⚠️ numbers.txt not found!")
            BOT_RUNNING = False
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping all workers...")
    # Clear queue to stop workers faster
    while not NUMBER_QUEUE.empty():
        try: NUMBER_QUEUE.get_nowait()
        except: break
    return {"status": "stopping"}

# --- WORKER LOGIC (10x Speed) ---
async def worker_loop(worker_id):
    while BOT_RUNNING:
        try:
            # Get number from queue (Non-blocking)
            number = NUMBER_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            break # Queue finished
        
        try:
            log_msg(f"🔹 Processing: {number}")
            await process_number(number)
        except Exception as e:
            log_msg(f"⚠️ Worker Error: {e}")
        
        await asyncio.sleep(1)
    
    if worker_id == 0: log_msg("✅ All tasks finished.")

# --- CORE LOGIC (Cleaned & Fast) ---
async def process_number(phone_number):
    proxy = get_proxy()
    target_country = SETTINGS["country"]
    
    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        }
        if proxy: launch_args["proxy"] = proxy

        try:
            browser = await p.chromium.launch(**launch_args)
            # Create context with mobile view
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()

            try:
                if not BOT_RUNNING: await browser.close(); return
                
                # Navigate
                await page.goto(BASE_URL, timeout=60000)
                
                # 1. Register
                # Use .first to be fast, minimal waiting
                try:
                    reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")).first
                    await reg.click(timeout=5000)
                except:
                    await browser.close(); return # Skip if site didn't load

                # 2. Agree (Tick Box)
                try:
                    cb = page.get_by_text("stay informed", exact=False).first
                    if await cb.count() > 0: await cb.click()
                    
                    agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)).first
                    await agree.click()
                except: pass

                # 3. DOB
                try:
                    await page.mouse.move(200, 500); await page.mouse.down()
                    await page.mouse.move(200, 800, steps=5); await page.mouse.up() # Fast scroll
                    dob = page.get_by_text("Next", exact=True).first
                    await dob.click()
                except: pass

                # 4. Phone Option
                try:
                    phone_opt = page.get_by_text("Use phone number", exact=False).first
                    await phone_opt.click()
                except: pass

                # 5. Country Switch (Arrow Logic)
                try:
                    # Check if already correct country? (Optimization)
                    # If not, try opening list
                    list_opened = False
                    
                    # Try Arrow
                    arrow = page.locator(".hwid-list-item-arrow").first
                    if await arrow.count() > 0: 
                        await arrow.click()
                        list_opened = True
                    else:
                        # Try Label Coords
                        label = page.get_by_text("Country/Region").first
                        if await label.count() > 0:
                            box = await label.bounding_box()
                            if box:
                                await page.touchscreen.tap(370, box['y'] + (box['height'] / 2))
                                list_opened = True
                    
                    if list_opened:
                        # Wait briefly for search input
                        search = page.get_by_placeholder("Search", exact=False).first
                        await search.click(timeout=3000)
                        await page.keyboard.type(target_country, delay=10) # Fast typing
                        await asyncio.sleep(1)
                        
                        matches = page.get_by_text(target_country, exact=False)
                        if await matches.count() > 1: await matches.nth(1).click()
                        else: await matches.first.click()
                except: pass

                # 6. Input Number
                try:
                    inp = page.locator("input[type='tel']").first
                    await inp.click()
                    await page.keyboard.type(phone_number, delay=10)
                    await page.touchscreen.tap(350, 100) # Hide KB
                    
                    get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                    await get_code.click()
                    
                    # 🔥 CHECK ERROR 🔥
                    try:
                        err = page.get_by_text("An unexpected problem", exact=False)
                        if await err.count() > 0:
                            log_msg(f"⛔ Error: {phone_number} Not Supported. Skipping.")
                            await browser.close(); return
                    except: pass

                    # 🔥 CAPTCHA LOOP 🔥
                    log_msg(f"⏳ Checking Captcha for {phone_number}...")
                    
                    start_time = time.time()
                    while time.time() - start_time < 60:
                        if not BOT_RUNNING: break
                        
                        # Fast check for captcha
                        captcha_frame = None
                        for frame in page.frames:
                            try:
                                if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                    captcha_frame = frame; break
                            except: pass
                        
                        if captcha_frame:
                            log_msg(f"🧩 Solving Captcha: {phone_number}")
                            await asyncio.sleep(5) # Mandatory wait for load
                            
                            session_id = f"sess_{random.randint(1000,9999)}"
                            solved = await solve_captcha(page, session_id)
                            
                            if not solved: 
                                await browser.close(); return
                            
                            await asyncio.sleep(5) # Wait for result
                            
                            # Verify
                            still_there = False
                            for frame in page.frames:
                                try:
                                    if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                        still_there = True; break
                                except: pass
                            
                            if not still_there:
                                log_msg(f"✅ SUCCESS: {phone_number} Verified!")
                                await browser.close(); return
                            else:
                                await asyncio.sleep(2); continue # Retry loop
                        
                        await asyncio.sleep(1)

                except Exception: pass

            except Exception: pass
            await browser.close()

        except Exception: pass