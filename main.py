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
    images = sorted([f"/captures/{f}" for f in os.listdir(CAPTURE_DIR) if f.endswith(".jpg")], key=lambda x: os.path.getmtime(f".{x}"), reverse=True)[:5]
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
            
            log_msg(f"🚀 Loaded {len(nums)} Numbers. Starting One-by-One...")
            bt.add_task(single_worker_loop)
        else: return {"status": "error"}
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 Stopping...")
    return {"status": "stopping"}

# --- SMART CLICK FUNCTION ---
async def smart_click(page, locator_func, step_name):
    """
    Waits up to 15 seconds. Checks every 1 second.
    If found -> Click.
    If not found -> Capture Error Screenshot.
    """
    if not BOT_RUNNING: return False
    
    # log_msg(f"👀 Finding: {step_name}...")
    
    for i in range(15): # 15 Seconds Wait
        try:
            element = locator_func()
            if await element.count() > 0 and await element.first.is_visible():
                await element.first.click()
                return True
        except: pass
        
        await asyncio.sleep(1) # Check every second
    
    # If loop finishes, Element NOT found
    log_msg(f"❌ Error: {step_name} Not Found! Taking Screenshot...")
    try:
        ts = time.strftime("%H%M%S")
        await page.screenshot(path=f"{CAPTURE_DIR}/Error_{step_name}_{ts}.jpg")
    except: pass
    return False

# --- MAIN LOOP ---
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
        await asyncio.sleep(2) # Rest between numbers

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
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()
            
            # 1. Load Home
            try:
                await page.goto(BASE_URL, timeout=60000, wait_until='domcontentloaded')
            except:
                log_msg(f"⚠️ Page Load Failed for {phone_number}. Retrying reload...")
                try: await page.reload(timeout=60000, wait_until='domcontentloaded')
                except: 
                    log_msg(f"❌ Failed to load site.")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Load_{phone_number}.jpg")
                    await browser.close(); return

            # 2. Register Button
            if not await smart_click(page, lambda: page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register")), "Register"):
                await browser.close(); return

            # 3. Checkbox & Agree
            # Click Checkbox Text
            if not await smart_click(page, lambda: page.get_by_text("stay informed", exact=False), "Checkbox_Text"):
                await browser.close(); return
            
            # Click Agree
            if not await smart_click(page, lambda: page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True)), "Agree_Btn"):
                await browser.close(); return

            # 4. DOB (Direct Click Next - No Scroll)
            if not await smart_click(page, lambda: page.get_by_text("Next", exact=True), "DOB_Next"):
                await browser.close(); return

            # 5. Use Phone Number
            if not await smart_click(page, lambda: page.get_by_text("Use phone number", exact=False), "Use_Phone_Btn"):
                await browser.close(); return

            # 6. Country Selection
            # Arrow Click
            arrow_clicked = await smart_click(page, lambda: page.locator(".hwid-list-item-arrow"), "Country_Arrow")
            if not arrow_clicked:
                # Try fallback coord click
                await page.touchscreen.tap(370, 150)
            
            # Wait for Search Box
            search_box = page.get_by_placeholder("Search", exact=False)
            try: await search_box.first.wait_for(state="visible", timeout=10000)
            except: 
                log_msg(f"❌ Country List not opened.")
                await page.screenshot(path=f"{CAPTURE_DIR}/Error_CountryList_{phone_number}.jpg")
                # Continue anyway, maybe country is already set?
            
            if await search_box.count() > 0:
                await search_box.first.click()
                await page.keyboard.type(target_country, delay=50)
                await asyncio.sleep(2)
                
                matches = page.get_by_text(target_country, exact=False)
                if await matches.count() > 1: await matches.nth(1).click()
                else: await matches.first.click()
                await asyncio.sleep(2)

            # 7. Input Number & Get Code
            inp = page.locator("input[type='tel']")
            try: await inp.first.wait_for(state="visible", timeout=10000)
            except:
                log_msg(f"❌ Input field not found.")
                await page.screenshot(path=f"{CAPTURE_DIR}/Error_Input_{phone_number}.jpg")
                await browser.close(); return

            await inp.first.click()
            await page.keyboard.type(phone_number, delay=20)
            await page.touchscreen.tap(350, 100) # Hide keyboard
            
            if not await smart_click(page, lambda: page.locator(".get-code-btn").or_(page.get_by_text("Get code")), "Get_Code_Btn"):
                await browser.close(); return

            # 8. Check for Immediate Error
            await asyncio.sleep(2)
            err_popup = page.get_by_text("An unexpected problem", exact=False)
            if await err_popup.count() > 0:
                log_msg(f"⛔ Error: {phone_number} Not Supported")
                await page.screenshot(path=f"{CAPTURE_DIR}/Error_Popup_{phone_number}.jpg")
                await browser.close(); return

            # 9. Captcha Handling
            start_time = time.time()
            captcha_found = False
            while time.time() - start_time < 10:
                if await page.get_by_text("swap 2 tiles", exact=False).count() > 0:
                    captcha_found = True; break
                await asyncio.sleep(1)

            if captcha_found:
                log_msg(f"🧩 Solving Captcha: {phone_number}")
                await asyncio.sleep(5)
                
                sess = f"s_{int(time.time())}"
                solved = await solve_captcha(page, sess)
                
                if not solved:
                    log_msg(f"⚠️ Captcha Solver Failed: {phone_number}")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Error_Solver_{phone_number}.jpg")
                else:
                    await asyncio.sleep(5)
                    # Check Success
                    if await page.get_by_text("swap 2 tiles", exact=False).count() == 0:
                        log_msg(f"✅ Success: {phone_number} Verified!")
                        # Capture Success Proof
                        await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")
                    else:
                        log_msg(f"❌ Captcha Verification Failed: {phone_number}")
                        await page.screenshot(path=f"{CAPTURE_DIR}/Error_Verify_{phone_number}.jpg")
            else:
                # No Captcha, Check if success
                if await page.locator(".get-code-btn").or_(page.get_by_text("Get code")).is_visible():
                     # Button still there means nothing happened
                     log_msg(f"⚠️ Timeout: {phone_number} (No reaction)")
                     await page.screenshot(path=f"{CAPTURE_DIR}/Error_Timeout_{phone_number}.jpg")
                else:
                    log_msg(f"✅ Success: {phone_number} (Direct)")
                    await page.screenshot(path=f"{CAPTURE_DIR}/Success_{phone_number}.jpg")

        except Exception as e:
            log_msg(f"🔥 Crash Error: {e}")
            try: await page.screenshot(path=f"{CAPTURE_DIR}/Error_Crash_{phone_number}.jpg")
            except: pass
        
        finally:
            if browser: await browser.close()