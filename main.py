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
    if len(logs) > 100: logs.pop()

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
    images = sorted([f"/captures/{f}" for f in os.listdir(CAPTURE_DIR) if f.endswith(".jpg")], key=lambda x: os.path.getmtime(os.path.join(CAPTURE_DIR, x)), reverse=True)[:5]
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
            
            log_msg(f"🚀 Loaded {len(nums)} Numbers. Starting Sequential Worker...")
            bt.add_task(single_worker_loop)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- 🔥 THE ZIDDI LOGIC (CHECK BACK) 🔥 ---
async def ensure_transition(page, current_btn_finder, next_page_indicator, step_name):
    """
    1. Click Current Button.
    2. Wait 3s.
    3. Check if Next Page Arrived? -> Success.
    4. If Not -> Check if Current Button still visible? -> Click Again (Retry).
    5. Repeat 3 times.
    """
    for attempt in range(1, 4):
        if not BOT_RUNNING: return False
        
        # 1. Check if we are ALREADY on next page (maybe previous click worked)
        try:
            if await next_page_indicator().count() > 0:
                # log_msg(f"✅ {step_name} Success (Next page found)")
                return True
        except: pass

        # 2. Try to Click Current Button
        try:
            btn = current_btn_finder()
            if await btn.count() > 0 and await btn.first.is_visible():
                if attempt > 1: log_msg(f"🔄 Retry {attempt}: Clicking {step_name} again...")
                
                await btn.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                await btn.first.click()
                await asyncio.sleep(3) # Wait for navigation
            else:
                # Button gone, but next page not found? Wait a bit more
                await asyncio.sleep(2)
        except Exception as e:
            pass
    
    # Final Check
    try:
        if await next_page_indicator().count() > 0: return True
    except: pass

    # If Failed
    log_msg(f"❌ Stuck at {step_name}. Next page not found.")
    try: await page.screenshot(path=f"{CAPTURE_DIR}/Stuck_{step_name}.jpg")
    except: pass
    return False

# --- WORKER LOOP ---
async def single_worker_loop():
    global BOT_RUNNING
    while BOT_RUNNING:
        try:
            phone_number = NUMBER_QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            log_msg("✅ Project Complete: All numbers processed.")
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
            
            # --- NAVIGATE ---
            try:
                await page.goto(BASE_URL, timeout=60000, wait_until='domcontentloaded')
            except:
                log_msg(f"⚠️ Load Failed: {phone_number}")
                await browser.close(); return

            # --- 1. REGISTER -> AGREE ---
            # Click Register, Verify 'Agree' page appears
            success = await ensure_transition(
                page,
                lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")),
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                "Register"
            )
            if not success: await browser.close(); return

            # --- 2. AGREE -> DOB ---
            # Click Checkbox first (Simple tap)
            try:
                cb = page.get_by_text("stay informed", exact=False).first
                if await cb.count() > 0: await cb.tap()
            except: pass

            # Click Agree, Verify 'DOB' (Next button) appears
            success = await ensure_transition(
                page,
                lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)),
                lambda: page.get_by_text("Next", exact=True), # DOB Next button
                "Agree_Page"
            )
            if not success: await browser.close(); return

            # --- 3. DOB -> PHONE OPTION (CRITICAL FIX) ---
            # Click DOB Next, Verify 'Use phone number' appears
            success = await ensure_transition(
                page,
                lambda: page.get_by_text("Next", exact=True), # DOB Next
                lambda: page.get_by_text("Use phone number", exact=False),
                "DOB_Page"
            )
            if not success: await browser.close(); return

            # --- 4. PHONE OPTION -> COUNTRY SELECTOR ---
            # Click Use Phone, Verify 'Country/Region' appears
            success = await ensure_transition(
                page,
                lambda: page.get_by_text("Use phone number", exact=False),
                lambda: page.locator(".hwid-list-item-arrow").or_(page.get_by_text("Country/Region")),
                "Phone_Option"
            )
            if not success: await browser.close(); return

            # --- 5. COUNTRY SWITCH ---
            try:
                # Just find the element to click
                arrow = page.locator(".hwid-list-item-arrow").first
                label = page.get_by_text("Country/Region").first
                
                # We use ensure_transition to make sure Search Box opens
                success = await ensure_transition(
                    page,
                    lambda: arrow if arrow else label, # Click Arrow
                    lambda: page.get_by_placeholder("Search", exact=False), # Wait for Search
                    "Country_Open"
                )
                
                if success:
                    search = page.get_by_placeholder("Search", exact=False).first
                    await search.click()
                    await page.keyboard.type(target_country, delay=50)
                    await asyncio.sleep(2)
                    
                    matches = page.get_by_text(target_country, exact=False)
                    if await matches.count() > 1: await matches.nth(1).click()
                    else: await matches.first.click()
                    await asyncio.sleep(2)
            except: pass

            # --- 6. INPUT NUMBER ---
            try:
                inp = page.locator("input[type='tel']").first
                if await inp.count() == 0:
                    log_msg(f"❌ Input missing: {phone_number}")
                    await browser.close(); return
                
                await inp.click()
                await page.keyboard.type(phone_number, delay=10)
                await page.touchscreen.tap(350, 100) # Hide KB
                
                # Click Get Code
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                await get_code.click()
                
                # Error Check
                await asyncio.sleep(2)
                if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                    log_msg(f"⛔ Not Supported: {phone_number}")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Popup_{phone_number}.jpg")
                    await browser.close(); return

                # Captcha Logic
                start = time.time()
                found = False
                while time.time() - start < 10:
                    if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                        found = True; break
                    await asyncio.sleep(1)
                
                if found:
                    log_msg(f"🧩 Solving Captcha: {phone_number}")
                    await asyncio.sleep(5)
                    sess = f"s_{random.randint(100,999)}"
                    solved = await solve_captcha(page, sess)
                    
                    await asyncio.sleep(5)
                    
                    if not solved:
                        # RETRY ONCE
                        log_msg(f"🔄 Retrying Captcha: {phone_number}")
                        await asyncio.sleep(5)
                        await solve_captcha(page, sess)
                        await asyncio.sleep(5)

                    # PROOF SCREENSHOT
                    await page.screenshot(path=f"{CAPTURE_DIR}/Proof_{phone_number}.jpg")

                    if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                        log_msg(f"✅ Success: {phone_number}")
                    else:
                        log_msg(f"❌ Failed: {phone_number} (Captcha Stuck)")
                else:
                    log_msg(f"✅ Success: {phone_number} (Direct)")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")

            except Exception as e:
                log_msg(f"⚠️ Input Error: {e}")

        except Exception as e:
            log_msg(f"🔥 System Error: {e}")
        
        finally:
            if browser: await browser.close()