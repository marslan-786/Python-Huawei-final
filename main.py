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
# کیپچر فولڈر اب صرف خالی پڑا رہے گا تاکہ ایرر نہ آئے
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"
CONCURRENT_WORKERS = 10  # 🔥 10 اصلی الگ الگ براؤزر

app = FastAPI()

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
logs = []
total_processed = 0
total_numbers = 0

def log_msg(message):
    entry = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 100: logs.pop()

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
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5: 
        return parse_proxy(SETTINGS["proxy_manual"])
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy(random.choice(lines))
        except: pass
    return None

# --- API ENDPOINTS ---

@app.get("/")
async def read_index():
    return FileResponse('index.html')

@app.get("/status")
async def get_status():
    return JSONResponse({
        "logs": logs, 
        "images": [], 
        "running": BOT_RUNNING,
        "current_country": SETTINGS["country"],
        "current_proxy": "Active" if get_proxy() else "None"
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
async def start_bot():
    global BOT_RUNNING, total_numbers, total_processed
    if not BOT_RUNNING:
        BOT_RUNNING = True
        total_processed = 0
        
        if os.path.exists(NUMBERS_FILE):
            with open(NUMBERS_FILE, "r") as f:
                nums = [l.strip() for l in f.readlines() if l.strip()]
            
            if not nums:
                log_msg("⚠️ Numbers file is empty!")
                BOT_RUNNING = False
                return {"status": "error"}

            # Clear Queue
            while not NUMBER_QUEUE.empty(): 
                try: NUMBER_QUEUE.get_nowait()
                except: break
            
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            total_numbers = len(nums)
            
            log_msg(f"🚀 Loaded {total_numbers} Numbers.")
            log_msg(f"🔥 Launching {CONCURRENT_WORKERS} Parallel Browsers...")
            
            # Start 10 Independent Workers
            for i in range(CONCURRENT_WORKERS):
                asyncio.create_task(worker_process(i+1))
        else:
            log_msg("numbers.txt missing!")
            BOT_RUNNING = False
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping all workers...")
    return {"status": "stopping"}

# --- THE WORKER (Lives as long as numbers exist) ---
async def worker_process(worker_id):
    global BOT_RUNNING, total_processed
    
    # Worker starts its OWN browser instance
    async with async_playwright() as p:
        try:
            proxy = get_proxy()
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            }
            if proxy: launch_args["proxy"] = proxy

            # Launch Browser ONCE per worker
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()
            
            # Loop through queue numbers
            while BOT_RUNNING:
                try:
                    phone_number = NUMBER_QUEUE.get_nowait()
                except asyncio.QueueEmpty:
                    break # No more numbers, close worker
                
                try:
                    log_msg(f"Processing: {phone_number}")
                    await process_single_number(page, phone_number)
                    
                    total_processed += 1
                    if total_processed >= total_numbers:
                        log_msg("✅✅ ALL NUMBERS COMPLETED ✅✅")
                        BOT_RUNNING = False
                        
                except Exception as e:
                    log_msg(f"Error: {phone_number} - {str(e)}")
                
                # Small delay between numbers for the same worker
                await asyncio.sleep(1)

            await browser.close()
            
        except Exception as e:
            log_msg(f"Worker {worker_id} Crashed: {e}")

# --- SINGLE NUMBER LOGIC (Optimized) ---
async def process_single_number(page, phone_number):
    target_country = SETTINGS["country"]
    
    try:
        # Navigate
        await page.goto(BASE_URL, timeout=40000)
        
        # 1. Register & Agree
        try:
            reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")).first
            await reg.click(timeout=5000)
            
            cb = page.get_by_text("stay informed", exact=False).first
            if await cb.count() > 0: await cb.click()
            
            agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)).first
            await agree.click()
        except:
            log_msg(f"Error: {phone_number} Load Failed")
            return

        # 2. DOB -> Phone
        try:
            # Fast Scroll
            await page.mouse.move(200, 500); await page.mouse.down()
            await page.mouse.move(200, 800, steps=2); await page.mouse.up() 
            
            dob = page.get_by_text("Next", exact=True).first
            await dob.click()
            
            phone_opt = page.get_by_text("Use phone number", exact=False).first
            await phone_opt.click()
        except:
            log_msg(f"Error: {phone_number} Navigation Error")
            return

        # 3. Country Switch
        try:
            list_opened = False
            # Check if arrow exists
            arrow = page.locator(".hwid-list-item-arrow").first
            if await arrow.count() > 0: 
                await arrow.click()
                list_opened = True
            else:
                # Fallback coord click
                await page.touchscreen.tap(370, 150)
                list_opened = True
            
            if list_opened:
                search = page.get_by_placeholder("Search", exact=False).first
                await search.click(timeout=3000)
                await page.keyboard.type(target_country, delay=10)
                await asyncio.sleep(1) # Wait for filter
                
                matches = page.get_by_text(target_country, exact=False)
                if await matches.count() > 1: await matches.nth(1).click()
                else: await matches.first.click()
        except: pass

        # 4. Input & Process
        try:
            inp = page.locator("input[type='tel']").first
            await inp.click()
            await page.keyboard.type(phone_number, delay=10)
            await page.touchscreen.tap(350, 100) # Hide KB
            
            get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
            await get_code.click()
            
            # Check for instant error
            await asyncio.sleep(1.5)
            err = page.get_by_text("An unexpected problem", exact=False)
            if await err.count() > 0:
                log_msg(f"Error: {phone_number} Not Supported (Skipping)")
                return

            # Captcha Wait Loop
            start_time = time.time()
            captcha_found = False
            
            while time.time() - start_time < 8: # Wait max 8s for captcha
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    captcha_found = True; break
                await asyncio.sleep(1)

            if captcha_found:
                log_msg(f"Solving Captcha: {phone_number}")
                await asyncio.sleep(5) # Allow image load
                
                # Solve
                session_id = f"sess_{random.randint(1000,9999)}"
                solved = await solve_captcha(page, session_id)
                
                if not solved: 
                    log_msg(f"Error: {phone_number} Solver Failed")
                    return
                
                await asyncio.sleep(5) # Wait for verification result
                
                if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                    log_msg(f"Success: {phone_number} Verified!")
                else:
                    log_msg(f"Error: {phone_number} Verification Failed")
            else:
                # No captcha appeared? Assume success or timeout
                # Double check error popup
                if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                    log_msg(f"Error: {phone_number} Not Supported")
                else:
                    # Sometimes it goes direct
                    log_msg(f"Success: {phone_number} Request Sent (No Captcha)")

        except Exception as e:
            log_msg(f"Error: {phone_number} Logic Failed")

    except Exception:
        pass