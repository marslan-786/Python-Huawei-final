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
MAX_WORKERS = 10 

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
logs = []
active_tasks = 0

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
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    return JSONResponse({
        "logs": logs, "images": [], "running": BOT_RUNNING,
        "current_country": SETTINGS["country"],
        "active_browsers": active_tasks
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
            
            bt.add_task(execution_manager, nums)
        else:
            log_msg("⚠️ numbers.txt not found!")
            BOT_RUNNING = False
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping... (Active browsers will close)")
    return {"status": "stopping"}

# --- MANAGER ---
async def execution_manager(numbers):
    sem = asyncio.Semaphore(MAX_WORKERS)
    log_msg(f"🚀 Started! Total: {len(numbers)} | Limit: {MAX_WORKERS} Browsers")
    
    tasks = []
    for i, number in enumerate(numbers):
        if not BOT_RUNNING: break
        
        await sem.acquire()
        
        # Stagger Start: Add random delay so proxies don't block
        delay = random.uniform(1, 3) if i < 10 else 0.5
        
        task = asyncio.create_task(browser_worker(sem, number, delay))
        tasks.append(task)

    await asyncio.gather(*tasks)
    if BOT_RUNNING: log_msg("✅✅ PROJECT COMPLETE: ALL NUMBERS DONE ✅✅")

# --- WORKER ---
async def browser_worker(sem, phone_number, start_delay):
    global active_tasks
    active_tasks += 1
    
    # Initial Wait to prevent CPU Spike
    await asyncio.sleep(start_delay)
    
    proxy_cfg = get_proxy()
    target_country = SETTINGS["country"]
    p_info = "Proxy" if proxy_cfg else "Direct"
    
    log_msg(f"🔵 Processing: {phone_number} ({p_info})")

    async with async_playwright() as p:
        browser = None
        try:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            }
            if proxy_cfg: launch_args["proxy"] = proxy_cfg

            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()

            # --- SMART NAVIGATION ---
            try:
                await page.goto(BASE_URL, timeout=60000, wait_until="domcontentloaded")
            except:
                log_msg(f"⚠️ {phone_number}: Load Retry 1...")
                try: await page.reload(timeout=60000, wait_until="domcontentloaded")
                except: raise Exception("Network Failed")

            # --- 1. REGISTER (Smart Wait) ---
            # Loop looking for Register button for 10 seconds
            reg_found = False
            for _ in range(10):
                if not BOT_RUNNING: break
                try:
                    reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")).first
                    if await reg.count() > 0:
                        await reg.click()
                        reg_found = True; break
                except: pass
                await asyncio.sleep(1)
            
            if not reg_found: raise Exception("Register Btn Missing")

            # --- 2. AGREE ---
            await asyncio.sleep(2) # Allow page load
            try:
                cb = page.get_by_text("stay informed", exact=False).first
                if await cb.count() > 0: await cb.click()
                
                agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)).first
                if await agree.count() > 0: await agree.click()
                else: raise Exception("Agree Btn Missing")
            except: raise Exception("Agree Phase Failed")

            # --- 3. DOB -> PHONE ---
            await asyncio.sleep(2)
            try:
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=5); await page.mouse.up() 
                
                dob = page.get_by_text("Next", exact=True).first
                await dob.click()
                await asyncio.sleep(1)
                
                ph_opt = page.get_by_text("Use phone number", exact=False).first
                await ph_opt.click()
            except: raise Exception("Nav Error (DOB/Phone)")

            # --- 4. COUNTRY SWITCH (The "Ziddi" Arrow Logic) ---
            await asyncio.sleep(2)
            try:
                list_opened = False
                # Try finding arrow class first
                arrow = page.locator(".hwid-list-item-arrow").first
                
                # Try clicking arrow/label
                if await arrow.count() > 0: await arrow.click()
                else: await page.touchscreen.tap(370, 150) # Coords
                
                # Check for Search Input
                await asyncio.sleep(1.5)
                search = page.get_by_placeholder("Search", exact=False).first
                if await search.count() > 0:
                    list_opened = True
                    await search.click()
                    await page.keyboard.type(target_country, delay=20)
                    await asyncio.sleep(2)
                    
                    matches = page.get_by_text(target_country, exact=False)
                    if await matches.count() > 1: await matches.nth(1).click()
                    else: await matches.first.click()
                else:
                    # If failed, assume default country or skip check to prevent crash
                    pass 
            except: pass

            # --- 5. INPUT NUMBER ---
            await asyncio.sleep(2)
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: raise Exception("Input Missing")
            
            await inp.click()
            await page.keyboard.type(phone_number, delay=10)
            await page.touchscreen.tap(350, 100)
            
            get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
            await get_code.click()
            
            # Error Popup Check
            await asyncio.sleep(2)
            if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                log_msg(f"⛔ Error: {phone_number} Not Supported")
                raise Exception("Not Supported")

            # --- 6. CAPTCHA ---
            start_time = time.time()
            captcha_found = False
            while time.time() - start_time < 10:
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    captcha_found = True; break
                await asyncio.sleep(1)

            if captcha_found:
                log_msg(f"🧩 Solving Captcha: {phone_number}")
                await asyncio.sleep(5)
                session_id = f"sess_{random.randint(1000,9999)}"
                solved = await solve_captcha(page, session_id)
                
                if not solved:
                    log_msg(f"⚠️ Error: {phone_number} Solver Failed")
                else:
                    await asyncio.sleep(5)
                    if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                        log_msg(f"✅ Success: {phone_number} Verified!")
                    else:
                        log_msg(f"❌ Error: {phone_number} Verification Failed")
            else:
                if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                    log_msg(f"⛔ Error: {phone_number} Not Supported")
                else:
                    log_msg(f"✅ Success: {phone_number} Sent (Direct)")

        except Exception as e:
            msg = str(e)
            if "Target closed" in msg: msg = "Browser Closed Unexpectedly"
            if "Timeout" in msg: msg = "Timeout / Slow Net"
            log_msg(f"⚠️ Error: {phone_number} - {msg}")
        
        finally:
            if browser: await browser.close()
            sem.release()
            active_tasks -= 1