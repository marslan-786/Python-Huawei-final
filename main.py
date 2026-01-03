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
STEP_TIMEOUT = 60000  # 60 Seconds Safety

app = FastAPI()

if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

try:
    from captcha_solver import solve_captcha
except ImportError:
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
    # 🔥 FIXED: Safe File Listing
    images = []
    try:
        files = [f for f in os.listdir(CAPTURE_DIR) if f.endswith(".jpg")]
        # Only take files that actually exist (Race condition fix)
        valid_files = []
        for f in files:
            full_path = os.path.join(CAPTURE_DIR, f)
            if os.path.exists(full_path):
                valid_files.append((f, os.path.getmtime(full_path)))
        
        # Sort by time desc
        valid_files.sort(key=lambda x: x[1], reverse=True)
        images = [f"/captures/{f[0]}" for f in valid_files[:5]]
    except Exception:
        pass # Ignore file errors to prevent crash

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
            
            log_msg(f"🚀 Loaded {len(nums)} Numbers.")
            bt.add_task(single_worker_loop)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- SAFE ACTION WRAPPER ---
async def do_step(page, locator_func, action_desc, action_type="click", input_text=""):
    if not BOT_RUNNING: return False
    log_msg(f"{action_desc}...")
    
    try:
        element = locator_func()
        await element.first.wait_for(state="visible", timeout=STEP_TIMEOUT)
        
        if action_type == "click": await element.first.click()
        elif action_type == "type": await element.first.fill(input_text)
        elif action_type == "tap": await element.first.tap()
            
        return True
    except Exception:
        log_msg(f"❌ Failed: {action_desc}")
        ts = time.strftime("%H%M%S")
        try: await page.screenshot(path=f"{CAPTURE_DIR}/Error_{ts}.jpg")
        except: pass
        return False

# --- WORKER ---
async def single_worker_loop():
    global BOT_RUNNING
    while BOT_RUNNING:
        try:
            phone_number = NUMBER_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            log_msg("✅ All numbers processed.")
            BOT_RUNNING = False
            break
        
        await process_number(phone_number)
        await asyncio.sleep(1)

async def process_number(phone_number):
    log_msg(f"🔵 Processing: {phone_number}")
    proxy_cfg = get_proxy()
    target_country = SETTINGS["country"]
    
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
            
            # 1. LOAD
            log_msg("🌐 Loading Website...")
            try:
                await page.goto(BASE_URL, timeout=STEP_TIMEOUT, wait_until='domcontentloaded')
            except:
                log_msg(f"❌ Load Failed")
                await browser.close(); return

            # 2. REGISTER
            if not await do_step(page, lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")), "👆 Tapping Register"):
                await browser.close(); return

            # 3. AGREE
            try:
                cb = page.get_by_text("stay informed", exact=False).first
                if await cb.count() > 0: await cb.tap()
            except: pass

            if not await do_step(page, lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), "👆 Tapping Agree"):
                await browser.close(); return

            # 4. DOB
            if not await do_step(page, lambda: page.get_by_text("Next", exact=True), "👆 Tapping DOB Next"):
                await browser.close(); return

            # 5. PHONE OPTION
            if not await do_step(page, lambda: page.get_by_text("Use phone number", exact=False), "👆 Tapping Use Phone"):
                await browser.close(); return

            # 6. COUNTRY SWITCH (Robust)
            log_msg("👆 Opening Country List...")
            list_opened = False
            
            # Try Arrow Class
            arrow = page.locator(".hwid-list-item-arrow").first
            if await arrow.count() > 0:
                await arrow.tap()
                list_opened = True
            else:
                # Try Label Coord Click
                label = page.get_by_text("Country/Region").first
                if await label.count() > 0:
                    box = await label.bounding_box()
                    if box:
                        await page.touchscreen.tap(370, box['y'] + 20)
                        list_opened = True
            
            if list_opened:
                # Wait for Search
                if not await do_step(page, lambda: page.get_by_placeholder("Search", exact=False), "⌨️ Typing Country", "type", target_country):
                    await browser.close(); return
                
                await asyncio.sleep(2)
                
                # Select Country
                matches = page.get_by_text(target_country, exact=False)
                if await matches.count() > 1: await matches.nth(1).click()
                else: await matches.first.click()
                await asyncio.sleep(2)

            # 7. INPUT
            if not await do_step(page, lambda: page.locator("input[type='tel']"), "⌨️ Typing Phone", "type", phone_number):
                await browser.close(); return
            
            await page.touchscreen.tap(350, 100) # Hide KB

            if not await do_step(page, lambda: page.locator(".get-code-btn").or_(page.get_by_text("Get code")), "👆 Tapping Get Code"):
                await browser.close(); return

            # 8. ERROR CHECK
            await asyncio.sleep(2)
            if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                log_msg(f"⛔ Error: Not Supported")
                await browser.close(); return

            # 9. CAPTCHA
            log_msg("⏳ Checking Captcha...")
            start = time.time()
            found = False
            while time.time() - start < 15:
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    found = True; break
                await asyncio.sleep(1)

            if found:
                log_msg(f"🧩 Solving Captcha...")
                await asyncio.sleep(3)
                sess = f"s_{random.randint(100,999)}"
                if await solve_captcha(page, sess):
                    await asyncio.sleep(5)
                    if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                        log_msg(f"✅ Success: Verified!")
                        await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")
                    else:
                        log_msg(f"❌ Failed: Captcha Stuck")
                        await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{phone_number}.jpg")
                else:
                    log_msg(f"⚠️ Solver Failed")
            else:
                if await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).is_visible():
                     log_msg(f"⚠️ Timeout: No reaction")
                else:
                    log_msg(f"✅ Success: Direct")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")

        except Exception as e:
            log_msg(f"🔥 Crash: {e}")
        
        finally:
            if browser: await browser.close()