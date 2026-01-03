import os
import glob
import asyncio
import random
import time
import shutil
import imageio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- IMPORT SOLVER ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    print("❌ ERROR: captcha_solver.py not found!")
    async def solve_captcha(page, session_id, logger=print): return False

# --- CONFIGURATION ---
CAPTURE_DIR = "./captures"
VIDEO_PATH = f"{CAPTURE_DIR}/proof.mp4"
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"

# --- GLOBAL SETTINGS ---
SETTINGS = {
    "country": "Russia",  # Default
    "proxy_manual": "",
    "use_proxy_file": False
}

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# --- GLOBAL STATE ---
BOT_RUNNING = False
logs = []

def log_msg(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

# --- PROXY HELPER ---
def get_current_proxy():
    # 1. Check Manual Input
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"].strip()) > 5:
        p = SETTINGS["proxy_manual"].strip()
        # Basic parsing assuming format protocol://user:pass@host:port or similar
        # For simplicity, passing directly if it looks valid, otherwise parsing logic needed
        # Here we assume user inputs standard proxy string
        if "http" in p or "socks" in p:
            return {"server": p} # Playwright will handle auth if in string
        else:
            return {"server": f"http://{p}"} 

    # 2. Check File
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, 'r') as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if lines:
            selected = random.choice(lines)
            return {"server": f"http://{selected}" if "://" not in selected else selected}
    
    # 3. No Proxy
    return None

def get_next_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f:
            lines = f.read().splitlines()
        for num in lines:
            if num.strip(): return num.strip()
    # Fallback generator if file empty
    prefix = "9"
    rest = ''.join([str(random.randint(0, 9)) for _ in range(9)])
    return f"{prefix}{rest}"

# --- API ENDPOINTS ---

@app.get("/")
async def read_index():
    return FileResponse('index.html')

@app.get("/status")
async def get_status():
    # Only sending file names, let frontend decide what to append
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    return JSONResponse({
        "logs": logs[:50], # Send last 50 logs only to save bandwidth
        "images": images,
        "running": BOT_RUNNING,
        "current_country": SETTINGS["country"]
    })

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country
    SETTINGS["proxy_manual"] = manual_proxy
    log_msg(f"⚙️ Settings Updated: Country={country}, ProxyMode={'Manual' if manual_proxy else 'Auto/None'}")
    return {"status": "updated"}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Proxy List Uploaded: {file.filename}")
    return {"status": "saved"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    log_msg(f"📂 Numbers File Uploaded: {file.filename}")
    return {"status": "saved"}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING:
        BOT_RUNNING = True
        bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    log_msg("🛑 STOP COMMAND RECEIVED.")
    return {"status": "stopping"}

@app.post("/clear_logs")
async def clear_logs_endpoint():
    global logs
    logs = []
    # Clean images too if requested? For now just logs
    return {"status": "cleared"}

@app.post("/generate_video")
async def trigger_video():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'))
    if not files: return {"status": "error", "error": "No images"}
    try:
        with imageio.get_writer(VIDEO_PATH, fps=10, format='FFMPEG', quality=8) as writer:
            for filename in files:
                try: writer.append_data(imageio.imread(filename))
                except: continue
        return {"status": "done"}
    except Exception as e: return {"status": "error", "error": str(e)}

# --- HELPER FUNCTIONS (Visual Tap etc same as before) ---
async def visual_tap(page, element, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            await page.evaluate(f"""
                var dot = document.createElement('div');
                dot.style.position = 'absolute'; left = '{x}px'; top = '{y}px';
                dot.style.width = '15px'; dot.style.height = '15px'; dot.style.background = 'rgba(255,0,0,0.6)';
                dot.style.borderRadius = '50%'; dot.style.zIndex = '999999'; dot.style.pointerEvents='none';
                document.body.appendChild(dot);
            """)
            log_msg(f"👆 Tapping {desc}...")
            await page.touchscreen.tap(x, y)
            return True
    except: pass
    return False

async def burst_wait(page, seconds, step_name):
    frames = int(seconds / 0.2)
    for i in range(frames):
        if not BOT_RUNNING: break
        ts = datetime.now().strftime("%H%M%S%f")
        try: await page.screenshot(path=f"{CAPTURE_DIR}/{ts}_{step_name}.jpg")
        except: pass
        await asyncio.sleep(0.2)

# --- CORE LOGIC LOOP ---
async def master_loop():
    current_number = get_next_number()
    retry_same_number = False

    while BOT_RUNNING:
        # Get settings dynamically each run
        target_country = SETTINGS["country"]
        proxy_cfg = get_current_proxy()
        
        if not retry_same_number: current_number = get_next_number()
        
        log_msg(f"🎬 STARTING | Country: {target_country} | Proxy: {'YES' if proxy_cfg else 'NO'}")
        
        result = await run_single_session(current_number, target_country, proxy_cfg)
        
        if result == "success":
            log_msg("🎉 Number Verified! Moving to next...")
            retry_same_number = False
        elif result == "retry":
            log_msg("⚠️ Retrying SAME Number...")
            retry_same_number = True
        else:
            break 
        
        await asyncio.sleep(2)

async def run_single_session(phone_number, country_name, proxy_config):
    async with async_playwright() as p:
        pixel_5 = p.devices['Pixel 5'].copy()
        pixel_5['user_agent'] = "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
        pixel_5['viewport'] = {'width': 412, 'height': 950} 

        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if proxy_config:
            launch_args["proxy"] = proxy_config

        browser = await p.chromium.launch(**launch_args)
        
        context = await browser.new_context(**pixel_5, locale="en-US")
        page = await context.new_page()

        try:
            if not BOT_RUNNING: return "stopped"
            log_msg("🚀 Navigating...")
            await page.goto(BASE_URL, timeout=60000)
            await burst_wait(page, 2, "01_load")

            # --- NAVIGATION LOGIC ---
            # 1. REGISTER
            reg_btn = page.get_by_text("Register", exact=True).first
            if await reg_btn.count() == 0: reg_btn = page.get_by_role("button", name="Register").first
            
            if await reg_btn.count() > 0:
                await visual_tap(page, reg_btn, "Register")
                await burst_wait(page, 2, "02_reg")
            else:
                log_msg("❌ Register button missing. Retry.")
                await browser.close(); return "retry"

            # 2. TERMS
            agree = page.get_by_text("Agree", exact=True).first
            if await agree.count() == 0: agree = page.get_by_text("Next", exact=True).first
            if await agree.count() > 0:
                await visual_tap(page, agree, "Terms")
                await burst_wait(page, 2, "03_terms")
            else:
                log_msg("❌ Agree/Next missing. Retry.")
                await browser.close(); return "retry"

            # 3. DOB
            await page.mouse.move(200, 500); await page.mouse.down()
            await page.mouse.move(200, 800, steps=10); await page.mouse.up()
            dob_next = page.get_by_text("Next", exact=True).first
            if await dob_next.count() > 0: 
                await visual_tap(page, dob_next, "DOB")
                await burst_wait(page, 2, "04_dob")

            # 4. PHONE OPTION
            use_phone = page.get_by_text("Use phone number", exact=False).first
            if await use_phone.count() > 0: 
                await visual_tap(page, use_phone, "PhoneOpt")
                await burst_wait(page, 2, "05_phone")

            # 5. COUNTRY SWITCH (Dynamic)
            log_msg(f"🌍 Switching Country to {country_name}...")
            hk = page.get_by_text("Hong Kong").first
            if await hk.count() == 0: hk = page.get_by_text("Country/Region").first
            
            if await hk.count() > 0:
                await visual_tap(page, hk, "Country")
                await burst_wait(page, 2, "06_list")
                
                search = page.locator("input").first
                if await search.count() > 0:
                    await visual_tap(page, search, "Search")
                    await page.keyboard.type(country_name, delay=50)
                    await burst_wait(page, 2, "07_typed")
                    
                    target_c = page.get_by_text(country_name, exact=False).first
                    if await target_c.count() > 0: 
                        await visual_tap(page, target_c, country_name)
                    else:
                        log_msg(f"❌ Country {country_name} not found!")
                        await browser.close(); return "retry"
                else:
                    log_msg("❌ Search box missing!")
                    await browser.close(); return "retry"
            else:
                log_msg("❌ Country selector missing!")
                await browser.close(); return "retry"

            # 6. INPUT NUMBER
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: inp = page.locator("input").first
            
            if await inp.count() > 0:
                await visual_tap(page, inp, "Input")
                for c in phone_number:
                    if not BOT_RUNNING: return "stopped"
                    await page.keyboard.type(c); await asyncio.sleep(0.05)
                await page.touchscreen.tap(350, 100) # Close KB
                
                get_code = page.locator(".get-code-btn").first
                if await get_code.count() == 0: get_code = page.get_by_text("Get code").first
                
                if await get_code.count() > 0:
                    await visual_tap(page, get_code, "GET CODE")
                    log_msg("⏳ Waiting for Captcha...")
                    
                    # --- CAPTCHA LOOP ---
                    start_time = time.time()
                    while BOT_RUNNING:
                        if time.time() - start_time > 60:
                            log_msg("⏰ Timeout.")
                            await browser.close(); return "retry"

                        captcha_frame = None
                        for frame in page.frames:
                            try:
                                if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                    captcha_frame = frame; break
                            except: pass
                        
                        if captcha_frame:
                            log_msg("🧩 CAPTCHA DETECTED.")
                            session_id = f"sess_{int(time.time())}"
                            ai_success = await solve_captcha(page, session_id, logger=log_msg)
                            
                            if not ai_success:
                                await browser.close(); return "retry"
                            
                            await burst_wait(page, 10, "11_check")
                            
                            is_still_there = False
                            for frame in page.frames:
                                try:
                                    if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                        is_still_there = True; break
                                except: pass
                            
                            if not is_still_there:
                                log_msg("✅ SUCCESS!")
                                await burst_wait(page, 3, "12_done")
                                await browser.close()
                                return "success"
                            else:
                                log_msg("🔁 Failed. Retrying...")
                                await asyncio.sleep(2)
                                continue
                        else:
                            await asyncio.sleep(1)
            
            await browser.close()
            return "retry"

        except Exception as e:
            log_msg(f"❌ Error: {str(e)}")
            await browser.close()
            return "retry"
