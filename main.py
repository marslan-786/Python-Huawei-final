import os
import glob
import asyncio
import random
import shutil
import cv2
import numpy as np
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
import uvicorn
from motor.motor_asyncio import AsyncIOMotorClient # 🔥 MongoDB Driver

# --- 🔥 DATABASE CONFIGURATION 🔥 ---
MONGO_URI = "mongodb://mongo:AEvrikOWlrmJCQrDTQgfGtqLlwhwLuAA@crossover.proxy.rlwy.net:29609"
DB_NAME = "huawei_training_data"
COLLECTION_NAME = "raw_captchas"

# --- SYSTEM PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")
NUMBERS_FILE = os.path.join(BASE_DIR, "numbers.txt")
PROXY_FILE = os.path.join(BASE_DIR, "proxies.txt")
BASE_URL = "https://id8.cloud.huawei.com/CAS/portal/login.html"

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

# --- GLOBAL VARS ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]

SETTINGS = {"country": "Default", "proxy_manual": ""}
BOT_RUNNING = False
logs = []
PROXY_INDEX = 0

# --- HELPERS ---
def log_msg(message, level="step"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

def count_file_lines(filepath):
    if not os.path.exists(filepath): return 0
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return len([line for line in f if line.strip()])
    except: return 0

def get_current_number_from_file():
    if not os.path.exists(NUMBERS_FILE): return None
    try:
        with open(NUMBERS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        return lines[0] if lines else None
    except: return None

def remove_current_number():
    if not os.path.exists(NUMBERS_FILE): return
    try:
        with open(NUMBERS_FILE, "r", encoding="utf-8", errors="ignore") as f: lines = f.readlines()
        new_lines = []
        removed = False
        for line in lines:
            if line.strip() and not removed: removed = True; continue
            new_lines.append(line)
        with open(NUMBERS_FILE, "w", encoding="utf-8") as f: f.writelines(new_lines)
    except: pass

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
                selected = lines[PROXY_INDEX]; PROXY_INDEX += 1
                return parse_proxy_string(selected)
        except: pass
    return None

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

# --- API ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    stats = { "remaining": count_file_lines(NUMBERS_FILE), "proxies": count_file_lines(PROXY_FILE) }
    return JSONResponse({"logs": logs[:50], "running": BOT_RUNNING, "stats": stats})

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country; SETTINGS["proxy_manual"] = manual_proxy
    return {"status": "updated"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    count = count_file_lines(NUMBERS_FILE)
    log_msg(f"📂 Numbers Uploaded. Total: {count}", level="main")
    return {"status": "saved", "count": count}

# 🔥 FIXED PROXY UPLOAD LOGIC 🔥
@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    count = count_file_lines(PROXY_FILE)
    log_msg(f"🌐 Proxies Uploaded. Total: {count}", level="main")
    return {"status": "saved", "count": count}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING: BOT_RUNNING = True; bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING; BOT_RUNNING = False
    log_msg("🛑 STOP COMMAND RECEIVED.", level="main")
    return {"status": "stopping"}

# --- 📸 DATA COLLECTOR ENGINE ---
async def save_captcha_to_mongo(image_bytes):
    try:
        doc = {
            "image": image_bytes,
            "timestamp": datetime.now(),
            "source": "huawei_collector_v1"
        }
        await collection.insert_one(doc)
        return True
    except Exception as e:
        log_msg(f"❌ DB Save Error: {e}")
        return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    if not get_current_number_from_file():
        log_msg("ℹ️ No Numbers File.", level="main"); BOT_RUNNING = False; return

    log_msg("🟢 Data Collector Started.", level="main")
    
    while BOT_RUNNING:
        current_number = get_current_number_from_file()
        if not current_number:
            log_msg("ℹ️ No Numbers Left.", level="main"); BOT_RUNNING = False; break
            
        proxy_cfg = get_current_proxy()
        p_show = proxy_cfg['server'] if proxy_cfg else "🌐 Direct"
        
        log_msg(f"🔵 Processing: {current_number}", level="main") 
        log_msg(f"🌍 Proxy: {p_show}", level="step") 
        
        try:
            # We don't care about success/fail now, just collecting
            await run_collector_session(current_number, proxy_cfg)
            remove_current_number() # Remove after processing (collecting 10 images)
            
        except Exception as e:
            log_msg(f"🔥 Crash: {e}", level="main")
        
        await asyncio.sleep(2)

async def run_collector_session(phone, proxy):
    try:
        async with async_playwright() as p:
            launch_args = { "headless": True, "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--ignore-certificate-errors", "--window-size=1920,1080"] }
            if proxy: launch_args["proxy"] = proxy 

            log_msg("🚀 Launching Collector...", level="step")
            try: browser = await p.chromium.launch(**launch_args)
            except Exception as e: log_msg(f"❌ Proxy Fail: {e}", level="main"); return

            context = await browser.new_context(viewport={'width': 1920, 'height': 1080}, locale="en-US")
            await context.clear_cookies()
            page = await context.new_page()

            log_msg("🌐 Opening Huawei...", level="step")
            try:
                if not BOT_RUNNING: return
                await page.goto(BASE_URL, timeout=60000) 
                await asyncio.sleep(5) 
                
                # Reg Flow
                reg_btn = page.get_by_text("Register", exact=True).or_(page.get_by_text("Sign up", exact=True))
                if await reg_btn.count() > 0: await reg_btn.first.click()
                else: log_msg("❌ Reg btn missing", level="main"); return
                await asyncio.sleep(5)

                phone_tab = page.get_by_text("Register with phone number")
                if await phone_tab.count() > 0: await phone_tab.first.click()
                await asyncio.sleep(2)
                
                final_phone = phone
                if phone.startswith("7") and len(phone) > 10: final_phone = phone[1:] 
                phone_input = page.get_by_placeholder("Phone")
                if await phone_input.count() > 0:
                    await phone_input.click(); await page.keyboard.type(final_phone, delay=100)
                else: return

                # Get Code
                get_code_btn = page.get_by_text("Get code", exact=True)
                if await get_code_btn.count() > 0:
                    await get_code_btn.first.click()
                    log_msg("⏳ Hard Wait: 10s for Captcha Check...", level="step")
                    await asyncio.sleep(10)
                    
                    # 🔥 COLLECTOR LOOP (10 Images per Number) 🔥
                    collected_count = 0
                    
                    # Pehle check karo agar captcha aaya hi nahi
                    puzzle_container = page.locator(".geetest_window").or_(page.locator(".nc_scale")).or_(page.locator("iframe[src*='captcha']"))
                    if await puzzle_container.count() == 0 and await page.get_by_text("Please complete verification").count() == 0:
                         log_msg("❌ No Captcha appeared on this number. Skipping.", level="main")
                         return

                    # Loop 10 times to get 10 variations
                    for i in range(1, 11): 
                        if not BOT_RUNNING: return
                        
                        log_msg(f"📸 Collection Round {i}/10...", level="step")
                        
                        # Find Image
                        puzzle_img = page.locator("img[src*='captcha']").first
                        if await puzzle_img.count() == 0: puzzle_img = page.locator(".geetest_canvas_bg").first
                        
                        if await puzzle_img.count() > 0:
                            # 1. Capture Raw Bytes (Clean Image - No Dots)
                            img_bytes = await puzzle_img.screenshot()
                            
                            # 2. Save to MongoDB
                            saved = await save_captcha_to_mongo(img_bytes)
                            if saved:
                                collected_count += 1
                                log_msg(f"✅ Saved to DB ({collected_count})", level="step")
                            
                            # 3. Wrong Slide Logic (To refresh captcha)
                            slider = page.locator(".geetest_slider_button").or_(page.locator(".nc_iconfont.btn_slide")).or_(page.locator(".yidun_slider"))
                            if await slider.count() > 0:
                                box = await slider.bounding_box()
                                if box:
                                    sx = box['x'] + box['width'] / 2
                                    sy = box['y'] + box['height'] / 2
                                    await page.mouse.move(sx, sy); await page.mouse.down()
                                    
                                    # Drag just a little bit (Wrongly) to fail it
                                    wrong_distance = random.randint(10, 30) 
                                    await page.mouse.move(sx + wrong_distance, sy + random.randint(-2, 2))
                                    await asyncio.sleep(0.2)
                                    await page.mouse.up()
                                    
                                    log_msg("🔄 Refreshing Captcha (Wait 10s)...", level="step")
                                    await asyncio.sleep(10) # Wait for new captcha
                        else:
                            log_msg("⚠️ Captcha disappeared early.", level="step")
                            break
                    
                    log_msg(f"🏁 Finished Collection. Total Saved: {collected_count}", level="main")

                else: return
            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}", level="main")
            finally:
                await browser.close()
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}", level="main")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")