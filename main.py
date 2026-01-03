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
STEP_TIMEOUT = 30000  # 🔥 30 Seconds Max per Step

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
    global BOT_RUNNING, total_numbers, total_processed
    if not BOT_RUNNING:
        BOT_RUNNING = True
        total_processed = 0
        if os.path.exists(NUMBERS_FILE):
            with open(NUMBERS_FILE, "r") as f: nums = [l.strip() for l in f.readlines() if l.strip()]
            
            while not NUMBER_QUEUE.empty(): NUMBER_QUEUE.get_nowait()
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            total_numbers = len(nums)
            
            log_msg(f"🚀 Loaded {total_numbers} Numbers. Starting Fast Worker...")
            bt.add_task(single_worker_loop)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- 🔥 HELPER: PERFORM ACTION WITH LOGS & SCREENSHOT 🔥 ---
async def do_step(page, locator_func, action_desc, action_type="click", input_text=""):
    """
    Handles logging, waiting, acting, and error capturing.
    action_type: 'click', 'type', 'wait'
    """
    if not BOT_RUNNING: return False
    
    log_msg(f"{action_desc}...") # Print start of action
    
    try:
        # Find Element
        element = locator_func()
        await element.first.wait_for(state="visible", timeout=STEP_TIMEOUT) # 30s Max
        
        if action_type == "click":
            await element.first.click()
        elif action_type == "type":
            await element.first.fill(input_text)
        elif action_type == "tap":
            await element.first.tap()
            
        return True
        
    except Exception as e:
        log_msg(f"❌ Failed: {action_desc} (Not Found)")
        # Capture Error Proof
        ts = time.strftime("%H%M%S")
        try: await page.screenshot(path=f"{CAPTURE_DIR}/Error_{ts}.jpg")
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
            
            # 1. LOAD WEBSITE
            log_msg("🌐 Loading Website...")
            try:
                await page.goto(BASE_URL, timeout=STEP_TIMEOUT, wait_until='domcontentloaded')
            except:
                log_msg(f"❌ Website Load Failed")
                await page.screenshot(path=f"{CAPTURE_DIR}/Error_Load.jpg")
                await browser.close(); return

            # 2. REGISTER
            if not await do_step(page, lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")), "👆 Tapping Register"):
                await browser.close(); return

            # 3. CHECKBOX
            # Note: We use 'tap' or 'click' depending on what works best, 'click' is safer for playwright
            if not await do_step(page, lambda: page.get_by_text("stay informed", exact=False), "👆 Tapping Checkbox"):
                pass # Checkbox might be optional or pre-checked, don't kill process

            # 4. AGREE
            if not await do_step(page, lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), "👆 Tapping Agree"):
                await browser.close(); return

            # 5. DOB NEXT
            # Direct wait and click
            if not await do_step(page, lambda: page.get_by_text("Next", exact=True), "👆 Tapping DOB Next"):
                await browser.close(); return

            # 6. USE PHONE NUMBER
            if not await do_step(page, lambda: page.get_by_text("Use phone number", exact=False), "👆 Tapping Use Phone Number"):
                await browser.close(); return

            # 7. OPEN COUNTRY LIST (ARROW)
            if not await do_step(page, lambda: page.locator(".hwid-list-item-arrow"), "👆 Tapping Country Arrow"):
                # Fallback to coords if arrow fails
                log_msg("⚠️ Arrow not found, trying coordinates...")
                await page.touchscreen.tap(370, 150)

            # 8. SEARCH INPUT
            if not await do_step(page, lambda: page.get_by_placeholder("Search", exact=False), "⌨️ Typing Country", action_type="type", input_text=target_country):
                await browser.close(); return
            
            await asyncio.sleep(1.5) # Wait for filter

            # 9. SELECT COUNTRY RESULT
            # Try to click the second item (result) or first if only one
            try:
                matches = page.get_by_text(target_country, exact=False)
                count = await matches.count()
                if count > 1:
                    log_msg("👆 Selecting Country Result")
                    await matches.nth(1).click()
                elif count == 1:
                    log_msg("👆 Selecting Country Result")
                    await matches.first.click()
                else:
                    log_msg("❌ Country Not Found in List")
                    await browser.close(); return
            except:
                log_msg("❌ Failed to select country")
                await browser.close(); return

            # 10. INPUT NUMBER
            if not await do_step(page, lambda: page.locator("input[type='tel']"), "⌨️ Typing Phone Number", action_type="type", input_text=phone_number):
                await browser.close(); return
            
            # Hide KB
            await page.touchscreen.tap(350, 100) 

            # 11. GET CODE
            if not await do_step(page, lambda: page.locator(".get-code-btn").or_(page.get_by_text("Get code")), "👆 Tapping Get Code"):
                await browser.close(); return

            # 12. CHECK ERROR POPUP
            await asyncio.sleep(2)
            if await page.get_by_text("An unexpected problem", exact=False).count() > 0:
                log_msg(f"⛔ Error: {phone_number} Not Supported")
                await page.screenshot(path=f"{CAPTURE_DIR}/Error_Supported_{phone_number}.jpg")
                await browser.close(); return

            # 13. CAPTCHA LOGIC
            log_msg("⏳ Checking for Captcha...")
            start_time = time.time()
            captcha_found = False
            while time.time() - start_time < 15: # 15s wait for captcha appearance
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    captcha_found = True; break
                await asyncio.sleep(1)

            if captcha_found:
                log_msg(f"🧩 Solving Captcha...")
                await asyncio.sleep(3)
                
                sess = f"s_{random.randint(100,999)}"
                solved = await solve_captcha(page, sess)
                
                await asyncio.sleep(5)
                
                if not solved:
                    log_msg(f"⚠️ Solver Failed")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Solver_{phone_number}.jpg")
                
                # Verification
                if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                    log_msg(f"✅ Success: {phone_number} Verified!")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")
                else:
                    log_msg(f"❌ Failed: {phone_number} (Captcha Stuck)")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Stuck_{phone_number}.jpg")
            else:
                # No Captcha?
                if await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).is_visible():
                     log_msg(f"⚠️ Timeout: Button still visible")
                     await page.screenshot(path=f"{CAPTURE_DIR}/Error_Timeout_{phone_number}.jpg")
                else:
                    log_msg(f"✅ Success: {phone_number} (Direct)")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")

        except Exception as e:
            log_msg(f"🔥 Crash: {e}")
            try: await page.screenshot(path=f"{CAPTURE_DIR}/Crash_{phone_number}.jpg")
            except: pass
        
        finally:
            if browser: await browser.close()