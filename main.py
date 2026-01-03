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
MAX_WORKERS = 1  # Sequential

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
    images = []
    try:
        if os.path.exists(CAPTURE_DIR):
            files = sorted([f for f in os.listdir(CAPTURE_DIR) if f.endswith(".jpg")], 
                           key=lambda x: os.path.getmtime(os.path.join(CAPTURE_DIR, x)), 
                           reverse=True)[:5]
            images = [f"/captures/{f}" for f in files]
    except: pass
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

# --- 🔥 HELPER: REAL CLICK (Force JS) 🔥 ---
async def real_click(element):
    """
    Tries 3 ways to click: Standard -> Force -> JS Execute
    """
    try:
        if await element.count() > 0:
            el = element.first
            await el.scroll_into_view_if_needed()
            # Try 1: Force Click (Playwright)
            try: await el.click(force=True, timeout=2000)
            except:
                # Try 2: JavaScript Click (The most powerful)
                await el.evaluate("e => e.click()")
            return True
    except: pass
    return False

# --- 🔥 ZIDDI TRANSITION LOGIC (SMART CHECKBOX) 🔥 ---
async def ensure_transition(page, current_finder, next_finder, step_name, handle_checkbox=False):
    for attempt in range(1, 6): # 5 Tries
        if not BOT_RUNNING: return False
        
        # 1. Check if Next Page is ALREADY here
        try:
            if await next_finder().count() > 0: return True
        except: pass

        # 2. Perform Action on Current Page
        try:
            btn = current_finder()
            if await btn.count() > 0:
                if attempt > 1: log_msg(f"♻️ {step_name}: Retry {attempt}...")
                
                # 🔥 SMART CHECKBOX LOGIC 🔥
                if handle_checkbox:
                    # Find checkbox (Text or Box)
                    cb_text = page.get_by_text("stay informed", exact=False).first
                    # Only click if NOT checked (checking generic checkbox status is hard on custom UIs, 
                    # so we assume if we are retrying, we might need to tick it again carefully)
                    # BUT BETTER: Just click the text, usually safe.
                    # To avoid toggle loop, we only click it ONCE per transition attempt sequence if possible, 
                    # or assume it's unticked if we are stuck.
                    # Let's try JS click on it.
                    await real_click(cb_text)
                    await asyncio.sleep(0.5)

                # Click Main Button (Next/Agree)
                await real_click(btn)
                
                await asyncio.sleep(3) # Wait for navigation
            else:
                log_msg(f"⏳ {step_name}: Waiting for button...")
                await asyncio.sleep(2)
        except Exception as e:
            pass
    
    # Final Check
    if await next_finder().count() > 0: return True
    
    log_msg(f"❌ Stuck at {step_name}.")
    ts = time.strftime("%H%M%S")
    try: await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{step_name}_{ts}.jpg")
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
        await asyncio.sleep(2)

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
            try:
                # log_msg("Navigating...")
                await page.goto(BASE_URL, timeout=40000, wait_until='networkidle') # Wait for network idle
            except:
                log_msg(f"⚠️ Load Failed: {phone_number}")
                await browser.close(); return

            # 2. REGISTER -> AGREE
            if not await ensure_transition(
                page,
                lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")),
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                "Register"
            ): await browser.close(); return

            # 3. AGREE -> DOB (WITH CHECKBOX HANDLING)
            # The ensure_transition will handle ticking + clicking Agree
            if not await ensure_transition(
                page,
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                lambda: page.get_by_text("Next", exact=True), # Target: DOB Next button
                "Agree_Terms",
                handle_checkbox=True # <--- Tells function to tick box
            ): await browser.close(); return

            # 4. DOB -> PHONE OPTION
            # Used JS click inside ensure_transition, so scroll shouldn't be an issue
            if not await ensure_transition(
                page,
                lambda: page.get_by_text("Next", exact=True),
                lambda: page.get_by_text("Use phone number", exact=False),
                "DOB_Next"
            ): await browser.close(); return

            # 5. PHONE OPTION -> COUNTRY
            if not await ensure_transition(
                page,
                lambda: page.get_by_text("Use phone number", exact=False),
                lambda: page.locator(".hwid-list-item-arrow").or_(page.get_by_text("Country/Region")),
                "Use_Phone_Btn"
            ): await browser.close(); return

            # 6. COUNTRY SWITCH
            try:
                log_msg("👆 Selecting Country...")
                # Open List
                opened = await ensure_transition(
                    page,
                    lambda: page.locator(".hwid-list-item-arrow").or_(page.get_by_text("Country/Region")),
                    lambda: page.get_by_placeholder("Search", exact=False),
                    "Country_List_Open"
                )
                
                if opened:
                    search = page.get_by_placeholder("Search", exact=False).first
                    await search.click()
                    await page.keyboard.type(target_country, delay=50)
                    await asyncio.sleep(2)
                    
                    matches = page.get_by_text(target_country, exact=False)
                    if await matches.count() > 1: await matches.nth(1).click()
                    else: await matches.first.click()
                    await asyncio.sleep(2)
            except: pass

            # 7. INPUT
            try:
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0:
                    log_msg(f"❌ Input missing: {phone_number}")
                    await browser.close(); return
                
                await inp.click()
                await page.keyboard.type(phone_number, delay=10)
                await page.touchscreen.tap(350, 100)
                
                # Click Get Code using JS force
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code"))
                await real_click(get_code)
                
                # Check Error
                await asyncio.sleep(2)
                if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                    log_msg(f"⛔ Not Supported: {phone_number}")
                    await browser.close(); return

                # Captcha Logic
                start = time.time()
                found = False
                while time.time() - start < 15:
                    if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                        found = True; break
                    await asyncio.sleep(1)
                
                if found:
                    log_msg(f"🧩 Solving Captcha...")
                    await asyncio.sleep(4)
                    sess = f"s_{random.randint(100,999)}"
                    solved = await solve_captcha(page, sess)
                    
                    await asyncio.sleep(5)
                    
                    if not solved:
                        log_msg(f"🔄 Retrying Captcha...")
                        await asyncio.sleep(5)
                        await solve_captcha(page, sess)
                        await asyncio.sleep(5)

                    if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                        log_msg(f"✅ Success: Verified!")
                        # await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")
                    else:
                        log_msg(f"❌ Failed: Captcha Stuck")
                        # await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{phone_number}.jpg")
                else:
                    if await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).is_visible():
                         log_msg(f"⚠️ Timeout: Button didn't react")
                    else:
                        log_msg(f"✅ Success: Direct")

            except Exception as e:
                log_msg(f"⚠️ Process Error: {e}")

        except Exception as e:
            log_msg(f"🔥 System Error: {e}")
        
        finally:
            if browser: await browser.close()