import os
import glob
import asyncio
import random
import string
import shutil
import imageio
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- 🔥 USER SETTINGS 🔥 ---
live_logs = True 

# --- CONFIGURATION ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
SUCCESS_FILE = "success.txt"
FAILED_FILE = "failed.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id8.cloud.huawei.com" 

# --- INITIALIZE ---
app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

SETTINGS = {"country": "Default", "proxy_manual": ""}
BOT_RUNNING = False
logs = []
CURRENT_RETRIES = 0 
PROXY_INDEX = 0

# --- HELPERS ---
def count_file_lines(filepath):
    if not os.path.exists(filepath): return 0
    try:
        with open(filepath, "r") as f:
            return len([l for l in f.readlines() if l.strip()])
    except: return 0

def log_msg(message, level="step"):
    if level == "step" and not live_logs: return
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

# --- FILE MANAGERS ---
def get_current_number_from_file():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: 
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if lines: return lines[0]
    return None

def remove_current_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: lines = f.readlines()
        if lines:
            with open(NUMBERS_FILE, "w") as f: f.writelines(lines[1:])

def save_to_file(filename, data):
    with open(filename, "a") as f: f.write(f"{data}\n")

# --- PROXY ---
def parse_proxy_string(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if p.count(":") == 3 and "://" not in p:
        parts = p.split(":")
        return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]}
    if "://" not in p: p = f"http://{p}"
    try:
        parsed = urlparse(p)
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username: cfg["username"] = parsed.username
        if parsed.password: cfg["password"] = parsed.password
        return cfg
    except: return None

def get_current_proxy():
    global PROXY_INDEX
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        return parse_proxy_string(SETTINGS["proxy_manual"])
    
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                if PROXY_INDEX >= len(lines): PROXY_INDEX = 0
                selected = lines[PROXY_INDEX]
                PROXY_INDEX += 1
                return parse_proxy_string(selected)
        except: pass
    return None

# --- API ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/download/{file_type}")
async def download_file(file_type: str):
    target_file = None
    if file_type == "numbers": target_file = NUMBERS_FILE
    elif file_type == "success": target_file = SUCCESS_FILE
    elif file_type == "failed": target_file = FAILED_FILE
    
    if target_file and os.path.exists(target_file):
        return FileResponse(target_file, filename=target_file, media_type='text/plain')
    return {"error": "File not found"}

@app.post("/clear_all")
async def clear_all_data():
    global logs
    logs = []
    open(NUMBERS_FILE, 'w').close()
    log_msg("🗑️ System & Numbers Cleared.", level="main")
    return {"status": "cleared"}

@app.post("/clear_proxies")
async def clear_proxies_api():
    SETTINGS["proxy_manual"] = ""
    open(PROXY_FILE, 'w').close()
    return {"status": "proxies_cleared"}

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)[:15]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    prox = get_current_proxy()
    p_disp = prox['server'] if prox else "🌐 Direct Internet"
    stats = {
        "remaining": count_file_lines(NUMBERS_FILE),
        "success": count_file_lines(SUCCESS_FILE),
        "failed": count_file_lines(FAILED_FILE)
    }
    return JSONResponse({"logs": logs[:50], "images": images, "running": BOT_RUNNING, "stats": stats, "current_proxy": p_disp})

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
    log_msg(f"📂 Numbers File Uploaded", level="main")
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
    log_msg("🛑 STOP COMMAND RECEIVED.", level="main")
    return {"status": "stopping"}

# --- VISUALS ---
async def capture_step(page, step_name, wait_time=0):
    if not BOT_RUNNING: return
    if wait_time > 0: await asyncio.sleep(wait_time)
    timestamp = datetime.now().strftime("%H%M%S")
    rnd = random.randint(10,99)
    filename = f"{CAPTURE_DIR}/{timestamp}_{step_name}_{rnd}.jpg"
    try: await page.screenshot(path=filename)
    except: pass

async def show_red_dot(page, x, y):
    try:
        await page.evaluate(f"""
            var dot = document.createElement('div');
            dot.style.position = 'absolute'; 
            dot.style.left = '{x-15}px'; dot.style.top = '{y-15}px';
            dot.style.width = '30px'; dot.style.height = '30px'; 
            dot.style.background = 'rgba(255, 0, 0, 0.8)'; 
            dot.style.borderRadius = '50%'; dot.style.zIndex = '2147483647'; 
            dot.style.pointerEvents = 'none'; dot.style.border = '4px solid yellow'; 
            dot.style.boxShadow = '0 0 15px rgba(0,0,0,0.8)';
            document.body.appendChild(dot);
            setTimeout(() => {{ dot.remove(); }}, 1500);
        """)
    except: pass

# --- CLICK LOGIC ---
async def execute_click_strategy(page, element, strategy_id, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if not box: return False
        
        cx = box['x'] + box['width'] / 2
        cy = box['y'] + box['height'] / 2
        
        await show_red_dot(page, cx, cy)
        await capture_step(page, f"Target_{desc}", wait_time=0.2)

        log_msg(f"🖱️ Clicking: {desc}", level="step")
        await element.click()
        return True
    except: return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    if not get_current_number_from_file():
        log_msg("ℹ️ No Numbers File.", level="main"); BOT_RUNNING = False; return

    log_msg("🟢 Worker Started.", level="main")
    
    while BOT_RUNNING:
        current_number = get_current_number_from_file()
        if not current_number:
            log_msg("ℹ️ No Numbers Left.", level="main"); BOT_RUNNING = False; break
            
        proxy_cfg = get_current_proxy()
        p_show = proxy_cfg['server'] if proxy_cfg else "🌐 Direct Internet"
        
        log_msg(f"🔵 Processing: {current_number}", level="main") 
        log_msg(f"🌍 Connection: {p_show}", level="step") 
        
        try:
            await run_huawei_session(current_number, proxy_cfg)
            
            log_msg("🏁 Cycle Complete. Next...", level="main")
            await asyncio.sleep(2)
            remove_current_number() # Proceed to next number

        except Exception as e:
            log_msg(f"🔥 Crash: {e}", level="main")
        
        await asyncio.sleep(2)

async def run_huawei_session(phone, proxy):
    try:
        async with async_playwright() as p:
            launch_args = {
                "headless": True, 
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--ignore-certificate-errors", "--window-size=1920,1080"]
            }
            if proxy: launch_args["proxy"] = proxy 

            log_msg("🚀 Launching PC Browser...", level="step")
            try: browser = await p.chromium.launch(**launch_args)
            except Exception as e: log_msg(f"❌ Proxy Fail: {e}", level="main"); return

            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US"
            )
            await context.clear_cookies()
            page = await context.new_page()

            log_msg("🌐 Opening Huawei Portal...", level="step")
            try:
                if not BOT_RUNNING: return
                await page.goto(BASE_URL, timeout=60000) 
                await asyncio.sleep(5) 
                
                # --- 1. GOTO REGISTER PAGE ---
                log_msg("🔎 Finding 'Register'...", level="step")
                reg_btn = page.get_by_text("Register", exact=True).or_(page.get_by_text("Sign up", exact=True))
                if await reg_btn.count() > 0:
                    await execute_click_strategy(page, reg_btn.first, 1, "Register_Link")
                    await asyncio.sleep(5)
                else:
                    log_msg("❌ Register link missing.", level="main"); return

                # --- 2. SELECT PHONE TAB ---
                phone_tab = page.get_by_text("Register with phone number")
                if await phone_tab.count() > 0:
                    log_msg("📱 Selecting Phone Tab...", level="step")
                    await execute_click_strategy(page, phone_tab.first, 1, "Phone_Tab")
                    await asyncio.sleep(2)
                
                # --- 3. INPUT PHONE NUMBER (SMART LOGIC) ---
                # Check: Only strip '7' if it STARTS with '7'
                final_phone = phone
                if phone.startswith("7") and len(phone) > 10:
                    final_phone = phone[1:] # Strip
                    log_msg(f"✨ Auto-trimmed +7: {final_phone}", level="step")
                else:
                    log_msg(f"✅ Number Kept As-Is: {final_phone}", level="step")
                
                phone_input = page.get_by_placeholder("Phone")
                if await phone_input.count() > 0:
                    await show_red_dot(page, 0, 0)
                    await phone_input.click()
                    await page.keyboard.type(final_phone, delay=100)
                    await capture_step(page, "03_Phone_Typed")
                else:
                    log_msg("❌ Phone input field not found.", level="main"); return

                # --- 4. CLICK GET CODE ---
                log_msg("📩 Clicking 'Get code'...", level="step")
                get_code_btn = page.get_by_text("Get code", exact=True)
                
                if await get_code_btn.count() > 0:
                    await execute_click_strategy(page, get_code_btn.first, 1, "Get_Code_Btn")
                    
                    # --- 🔥 5. 30-SECOND OBSERVATION LOOP 🔥 ---
                    log_msg("⏳ Waiting 30s for Code (Capturing every 5s)...", level="main")
                    
                    for i in range(1, 7): # 1 to 6 (6 * 5 = 30s)
                        if not BOT_RUNNING: break
                        await asyncio.sleep(5)
                        await capture_step(page, f"05_Wait_Step_{i}_(5s)")
                        log_msg(f"📸 Capture {i}/6", level="step")
                    
                    log_msg("🏁 Observation Finished.", level="main")

                else:
                    log_msg("❌ 'Get code' button missing.", level="main"); return

            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}", level="main")
                await capture_step(page, "Session_Crash")
            finally:
                await browser.close()
                
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}", level="main")