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
CONCURRENT_WORKERS = 10 

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

def get_rotated_proxy():
    """
    Returns (ProxyConfig, ProxyName)
    Prioritizes Manual -> File -> Direct
    """
    # 1. Manual Proxy (Fixed)
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        p_cfg = parse_proxy(SETTINGS["proxy_manual"])
        if p_cfg: return p_cfg, f"Manual ({p_cfg['server']})"

    # 2. File Proxy (Rotated)
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                # Random choice acts as infinite rotation
                selected = random.choice(lines)
                p_cfg = parse_proxy(selected)
                if p_cfg: return p_cfg, f"Rotated ({p_cfg['server']})"
        except: pass
    
    # 3. No Proxy (Direct)
    return None, "Direct IP"

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
        "current_proxy": "Active" # Placeholder for UI
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
            
            # --- STARTUP LOGS ---
            log_msg(f"📥 Loaded: {total_numbers} Numbers")
            
            # Check Proxy Status
            sample_proxy, p_type = get_rotated_proxy()
            if sample_proxy:
                log_msg(f"🌍 Mode: Starting WITH Proxies")
            else:
                log_msg(f"⚠️ Mode: Starting WITHOUT Proxy (Direct)")

            log_msg(f"🔥 Launching {CONCURRENT_WORKERS} Workers...")
            
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

# --- WORKER PROCESS ---
async def worker_process(worker_id):
    global BOT_RUNNING, total_processed
    
    while BOT_RUNNING:
        # Each iteration = New Browser Instance (Clean Slate)
        # This ensures clean proxy usage per number
        try:
            try:
                phone_number = NUMBER_QUEUE.get_nowait()
            except asyncio.QueueEmpty:
                break # Queue finished
            
            # Get New Proxy for this specific attempt
            proxy_cfg, proxy_name = get_rotated_proxy()
            
            log_msg(f"Processing: {phone_number} | Proxy: {proxy_name}")
            
            await process_single_number(phone_number, proxy_cfg)
            
            total_processed += 1
            if total_processed >= total_numbers:
                log_msg("✅✅ ALL NUMBERS COMPLETED ✅✅")
                BOT_RUNNING = False
            
            await asyncio.sleep(1)

        except Exception as e:
            log_msg(f"Worker Error: {e}")
            await asyncio.sleep(2)

# --- SINGLE NUMBER LOGIC ---
async def process_single_number(phone_number, proxy_cfg):
    target_country = SETTINGS["country"]
    
    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        }
        if proxy_cfg: launch_args["proxy"] = proxy_cfg

        try:
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()

            try:
                if not BOT_RUNNING: await browser.close(); return
                
                # Navigate (With Proxy Check)
                try:
                    await page.goto(BASE_URL, timeout=40000)
                except Exception:
                    log_msg(f"❌ Error: Proxy Connection Failed for {phone_number}")
                    await browser.close(); return

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
                    await browser.close(); return

                # 2. DOB -> Phone
                try:
                    await page.mouse.move(200, 500); await page.mouse.down()
                    await page.mouse.move(200, 800, steps=2); await page.mouse.up() 
                    
                    dob = page.get_by_text("Next", exact=True).first
                    await dob.click()
                    
                    phone_opt = page.get_by_text("Use phone number", exact=False).first
                    await phone_opt.click()
                except:
                    await browser.close(); return

                # 3. Country Switch
                try:
                    list_opened = False
                    arrow = page.locator(".hwid-list-item-arrow").first
                    if await arrow.count() > 0: 
                        await arrow.click()
                        list_opened = True
                    else:
                        await page.touchscreen.tap(370, 150)
                        list_opened = True
                    
                    if list_opened:
                        search = page.get_by_placeholder("Search", exact=False).first
                        await search.click(timeout=3000)
                        await page.keyboard.type(target_country, delay=10)
                        await asyncio.sleep(1)
                        
                        matches = page.get_by_text(target_country, exact=False)
                        if await matches.count() > 1: await matches.nth(1).click()
                        else: await matches.first.click()
                except: pass

                # 4. Input & Process
                try:
                    inp = page.locator("input[type='tel']").first
                    await inp.click()
                    await page.keyboard.type(phone_number, delay=10)
                    await page.touchscreen.tap(350, 100) 
                    
                    get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                    await get_code.click()
                    
                    # Error Popup
                    await asyncio.sleep(1.5)
                    err = page.get_by_text("An unexpected problem", exact=False)
                    if await err.count() > 0:
                        log_msg(f"Error: {phone_number} Not Supported (Skipping)")
                        await browser.close(); return

                    # Captcha Logic
                    start_time = time.time()
                    captcha_found = False
                    
                    while time.time() - start_time < 8:
                        if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                            captcha_found = True; break
                        await asyncio.sleep(1)

                    if captcha_found:
                        log_msg(f"Solving Captcha: {phone_number}")
                        await asyncio.sleep(5) 
                        
                        session_id = f"sess_{random.randint(1000,9999)}"
                        solved = await solve_captcha(page, session_id)
                        
                        if not solved: 
                            log_msg(f"Error: {phone_number} Solver Failed")
                            await browser.close(); return
                        
                        await asyncio.sleep(5) 
                        
                        if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                            log_msg(f"Success: {phone_number} Verified!")
                        else:
                            log_msg(f"Error: {phone_number} Verification Failed")
                    else:
                        if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                            log_msg(f"Error: {phone_number} Not Supported")
                        else:
                            log_msg(f"Success: {phone_number} Sent (Direct)")

                except Exception: pass

            except Exception: pass
            await browser.close()

        except Exception:
            # This catches Browser Launch errors (Proxy Failures)
            log_msg(f"❌ Error: Proxy Connection Failed for {phone_number}")