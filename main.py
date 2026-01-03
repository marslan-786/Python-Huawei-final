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
MAX_WORKERS = 10  # 🔥 صرف 10 براؤزر کی اجازت

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
active_tasks = 0 # ٹریکنگ کے لیے

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
            
            # Start the Manager in Background
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

# --- THE MANAGER (SEMAPHORE LOGIC) ---
async def execution_manager(numbers):
    # 🔥 یہ سب سے اہم لائن ہے۔ یہ صرف 10 ٹوکن جاری کرے گا۔
    sem = asyncio.Semaphore(MAX_WORKERS)
    
    log_msg(f"🚀 Started! Total: {len(numbers)} | Limit: {MAX_WORKERS} Browsers")
    
    tasks = []
    for number in numbers:
        if not BOT_RUNNING: break
        
        # یہ لائن تب تک رکی رہے گی جب تک کوئی پرانا براؤزر بند نہ ہو جائے
        await sem.acquire()
        
        # جیسے ہی ٹوکن ملا، نیا ٹاسک شروع
        task = asyncio.create_task(browser_worker(sem, number))
        tasks.append(task)
        
        # تھوڑا سا وقفہ تاکہ ایک ساتھ 10 نہ کھلیں (سسٹم سیفٹی)
        await asyncio.sleep(0.5)

    # سب کے ختم ہونے کا انتظار
    await asyncio.gather(*tasks)
    if BOT_RUNNING: log_msg("✅✅ PROJECT COMPLETE: ALL NUMBERS DONE ✅✅")

# --- THE WORKER (Opens 1 Browser, Does Work, Closes) ---
async def browser_worker(sem, phone_number):
    global active_tasks
    active_tasks += 1
    
    proxy_cfg = get_proxy()
    target_country = SETTINGS["country"]
    
    # 🟢 START LOG (Browser Opening)
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

            # 🔥 NEW BROWSER PER NUMBER
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()

            # --- NAVIGATION & LOGIC ---
            if not BOT_RUNNING: raise Exception("Stopped")
            
            try:
                await page.goto(BASE_URL, timeout=40000)
            except:
                log_msg(f"❌ Error: {phone_number} - Network/Proxy Failed")
                raise Exception("Network Error")

            # 1. Register & Agree
            try:
                reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")).first
                await reg.click(timeout=8000)
                
                cb = page.get_by_text("stay informed", exact=False).first
                if await cb.count() > 0: await cb.click()
                
                agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)).first
                await agree.click()
            except:
                log_msg(f"⚠️ Error: {phone_number} - Page Load Failed")
                raise Exception("Load Failed")

            # 2. DOB -> Phone
            try:
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=2); await page.mouse.up() 
                await page.get_by_text("Next", exact=True).first.click()
                await page.get_by_text("Use phone number", exact=False).first.click()
            except:
                raise Exception("Nav Error")

            # 3. Country Switch
            try:
                list_opened = False
                arrow = page.locator(".hwid-list-item-arrow").first
                if await arrow.count() > 0: await arrow.click(); list_opened = True
                else: await page.touchscreen.tap(370, 150); list_opened = True
                
                if list_opened:
                    search = page.get_by_placeholder("Search", exact=False).first
                    await search.click(timeout=5000)
                    await page.keyboard.type(target_country, delay=10)
                    await asyncio.sleep(1)
                    matches = page.get_by_text(target_country, exact=False)
                    if await matches.count() > 1: await matches.nth(1).click()
                    else: await matches.first.click()
            except: pass

            # 4. Input & Process
            inp = page.locator("input[type='tel']").first
            await inp.click()
            await page.keyboard.type(phone_number, delay=10)
            await page.touchscreen.tap(350, 100) 
            
            await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first.click()
            
            # Error Check
            await asyncio.sleep(1.5)
            if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                log_msg(f"⛔ Error: {phone_number} Not Supported")
                raise Exception("Not Supported")

            # Captcha Logic
            start_time = time.time()
            captcha_found = False
            while time.time() - start_time < 8:
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

        except Exception:
            pass # Logs handled inside specific blocks
        
        finally:
            if browser: await browser.close()
            # 🔥 سب سے اہم: ٹوکن واپس کرنا تاکہ اگلا نمبر شروع ہو سکے
            sem.release()
            active_tasks -= 1