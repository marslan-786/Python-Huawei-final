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
total_processed = 0
total_numbers = 0

def log_msg(message):
    entry = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 100: logs.pop()

def parse_proxy(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if "://" not in p: p = f"http://{p}"
    try:
        u = urlparse(p)
        return {"server": f"{u.scheme}://{u.hostname}:{u.port}", "username": u.username, "password": u.password} if u.username else {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    except: return None

def get_proxy():
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5: return parse_proxy(SETTINGS["proxy_manual"])
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy(random.choice(lines))
        except: pass
    return None

# --- API ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    # Show latest images if available
    images = sorted([f"/captures/{f}" for f in os.listdir(CAPTURE_DIR) if f.endswith(".jpg")], reverse=True)[:5]
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
    global BOT_RUNNING, total_numbers, total_processed
    if not BOT_RUNNING:
        BOT_RUNNING = True
        total_processed = 0
        if os.path.exists(NUMBERS_FILE):
            with open(NUMBERS_FILE, "r") as f: nums = [l.strip() for l in f.readlines() if l.strip()]
            if not nums: return {"status": "error"}
            
            while not NUMBER_QUEUE.empty(): NUMBER_QUEUE.get_nowait()
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            total_numbers = len(nums)
            
            log_msg(f"🚀 Loaded {total_numbers} Numbers.")
            log_msg(f"🔥 Starting {MAX_WORKERS} Fixed Workers...")
            
            for i in range(MAX_WORKERS):
                bt.add_task(worker_loop, i+1)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- WORKER ---
async def worker_loop(worker_id):
    global BOT_RUNNING, total_processed
    await asyncio.sleep(worker_id * 2) 
    
    while BOT_RUNNING:
        try:
            phone_number = NUMBER_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            break
        
        try:
            log_msg(f"🔵 Processing: {phone_number} (Worker {worker_id})")
            await process_single_number(phone_number)
            
            total_processed += 1
            if total_processed >= total_numbers:
                log_msg("✅✅ ALL NUMBERS DONE ✅✅")
                BOT_RUNNING = False
                
        except Exception as e:
            log_msg(f"Worker Error: {e}")
        
        await asyncio.sleep(1)

# --- CORE LOGIC ---
async def process_single_number(phone_number):
    target_country = SETTINGS["country"]
    proxy_cfg = get_proxy()
    
    async with async_playwright() as p:
        browser = None
        try:
            launch_args = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
            if proxy_cfg: launch_args["proxy"] = proxy_cfg
            
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950}, 
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36",
                has_touch=True
            )
            page = await context.new_page()
            
            try:
                await page.goto(BASE_URL, timeout=60000, wait_until='domcontentloaded')
            except:
                log_msg(f"⚠️ {phone_number}: Load Failed")
                await browser.close(); return

            # 1. Register
            try:
                reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")).first
                await reg.wait_for(state="visible", timeout=10000)
                await reg.tap() 
            except:
                log_msg(f"⚠️ {phone_number}: Register Btn Missing")
                await browser.close(); return

            # 2. Agree
            try:
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)
                cb = page.get_by_text("stay informed", exact=False).first
                await cb.tap()
                await asyncio.sleep(1) 
                
                agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)).first
                await agree.tap()
            except:
                log_msg(f"⚠️ {phone_number}: Agree Phase Failed")
                await browser.close(); return

            # 3. DOB -> Phone (FIXED SCROLLING)
            try:
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)
                
                # 🔥 FIX: Force Scroll into View
                dob_next = page.get_by_text("Next", exact=True).first
                await dob_next.scroll_into_view_if_needed()
                await asyncio.sleep(1) # Wait after scroll
                await dob_next.tap()
                
                phone_opt = page.get_by_text("Use phone number", exact=False).first
                await phone_opt.wait_for(state="visible", timeout=5000)
                await phone_opt.tap()
            except:
                log_msg(f"⚠️ {phone_number}: DOB/Phone Failed")
                await browser.close(); return

            # 4. Country Switch
            try:
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)
                
                arrow = page.locator(".hwid-list-item-arrow").first
                if await arrow.count() > 0: await arrow.tap()
                else: await page.touchscreen.tap(370, 150)
                
                search = page.get_by_placeholder("Search", exact=False).first
                await search.wait_for(state="visible", timeout=5000)
                await search.tap()
                await page.keyboard.type(target_country, delay=20)
                await asyncio.sleep(1)
                
                matches = page.get_by_text(target_country, exact=False)
                if await matches.count() > 1: await matches.nth(1).tap()
                else: await matches.first.tap()
            except: pass

            # 5. Input Number
            try:
                await asyncio.sleep(2)
                inp = page.locator("input[type='tel']").first
                await inp.tap()
                await page.keyboard.type(phone_number, delay=10)
                await page.touchscreen.tap(350, 100)
                
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                await get_code.tap()
                
                await asyncio.sleep(2)
                if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                    log_msg(f"⛔ {phone_number}: Not Supported")
                    await browser.close(); return

                # 6. CAPTCHA LOGIC (RETRY & PROOF)
                start = time.time()
                found = False
                while time.time() - start < 10:
                    if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                        found = True; break
                    await asyncio.sleep(1)
                
                if found:
                    log_msg(f"🧩 Solving Captcha: {phone_number}")
                    
                    # ATTEMPT 1
                    sess = f"s_{random.randint(100,999)}"
                    await solve_captcha(page, sess)
                    await asyncio.sleep(5)
                    
                    # CHECK IF STILL THERE (RETRY LOGIC)
                    if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                        log_msg(f"🔄 {phone_number}: Captcha Retrying in 10s...")
                        await asyncio.sleep(10) # Wait 10s
                        await solve_captcha(page, sess) # Try again
                        await asyncio.sleep(5)

                    # 📸 CAPTURE PROOF (Success or Fail)
                    timestamp = time.strftime("%H%M%S")
                    proof_file = f"{CAPTURE_DIR}/{phone_number}_{timestamp}.jpg"
                    await page.screenshot(path=proof_file)

                    # FINAL VERIFICATION
                    is_captcha = await page.get_by_text("swap 2 tiles", exact=False).count() > 0
                    is_get_code = await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).is_visible()
                    
                    if not is_captcha and not is_get_code:
                        log_msg(f"✅ Success: {phone_number} (Proof Saved)")
                    else:
                        log_msg(f"❌ Failed: {phone_number} (Captcha Stuck)")
                else:
                    if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                        log_msg(f"⛔ {phone_number}: Not Supported")
                    else:
                        log_msg(f"✅ Success: {phone_number} (Direct)")

            except Exception as e:
                log_msg(f"⚠️ {phone_number}: Input Error - {e}")

        except Exception:
            log_msg(f"❌ {phone_number}: Browser Error")
        
        finally:
            if browser: await browser.close()