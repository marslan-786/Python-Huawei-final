import os
import glob
import asyncio
import random
import cv2
import numpy as np
import ddddocr
from rembg import remove
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
import uvicorn
import shutil

# --- 🔥 SYSTEM PATHS (BULLETPROOF) 🔥 ---
# Get the absolute path of the directory where main.py is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")
NUMBERS_FILE = os.path.join(BASE_DIR, "numbers.txt")
SUCCESS_FILE = os.path.join(BASE_DIR, "success.txt")
FAILED_FILE = os.path.join(BASE_DIR, "failed.txt")
PROXY_FILE = os.path.join(BASE_DIR, "proxies.txt")
BASE_URL = "https://id8.cloud.huawei.com/CAS/portal/login.html"

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

SETTINGS = {"country": "Default", "proxy_manual": ""}
BOT_RUNNING = False
logs = []
CURRENT_RETRIES = 0 
PROXY_INDEX = 0

# 🔥 INITIALIZE CHINESE ENGINE 🔥
ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

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
    if not os.path.exists(NUMBERS_FILE):
        log_msg(f"⚠️ DEBUG: File not found at {NUMBERS_FILE}", level="step")
        return None
    try:
        with open(NUMBERS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if not lines:
            log_msg("⚠️ DEBUG: File exists but is EMPTY!", level="step")
            return None
        return lines[0]
    except Exception as e:
        log_msg(f"⚠️ Read Error: {e}", level="step")
        return None

def remove_current_number():
    if not os.path.exists(NUMBERS_FILE): return
    try:
        with open(NUMBERS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        new_lines = []
        removed = False
        for line in lines:
            if line.strip() and not removed:
                removed = True
                continue
            new_lines.append(line)
        with open(NUMBERS_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except: pass

def save_to_file(filename, data):
    with open(filename, "a", encoding="utf-8") as f: f.write(f"{data}\n")

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
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)[:15]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    prox = get_current_proxy()
    p_disp = prox['server'] if prox else "🌐 Direct Internet"
    stats = { "remaining": count_file_lines(NUMBERS_FILE), "success": count_file_lines(SUCCESS_FILE), "failed": count_file_lines(FAILED_FILE) }
    return JSONResponse({"logs": logs[:50], "images": images, "running": BOT_RUNNING, "stats": stats, "current_proxy": p_disp})

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country; SETTINGS["proxy_manual"] = manual_proxy
    return {"status": "updated"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    try:
        content = await file.read() # Read full content to memory
        text_content = content.decode("utf-8", errors="ignore")
        
        # Split by newlines and filter empty lines
        lines = [l.strip() for l in text_content.splitlines() if l.strip()]
        
        # Save clean content
        with open(NUMBERS_FILE, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(f"{line}\n")
        
        count = len(lines)
        log_msg(f"📂 Numbers Uploaded. Path: {NUMBERS_FILE}", level="main")
        log_msg(f"🔢 Total Lines Found: {count}", level="main")
        
        if count > 0:
            log_msg(f"👀 First Number: {lines[0]}", level="step")
        else:
            log_msg("⚠️ WARNING: File is empty after processing!", level="main")
            
        return {"status": "saved", "count": count}
    except Exception as e:
        log_msg(f"❌ Upload Error: {e}", level="main")
        return {"error": str(e)}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    try:
        with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
        return {"status": "saved"}
    except: return {"status": "error"}

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

# --- VISUALS ---
async def capture_step(page, step_name, wait_time=0):
    if not BOT_RUNNING: return
    if wait_time > 0: await asyncio.sleep(wait_time)
    ts = datetime.now().strftime("%H%M%S")
    rnd = random.randint(10,99)
    filename = f"{CAPTURE_DIR}/{ts}_{step_name}_{rnd}.jpg"
    try: await page.screenshot(path=filename)
    except: pass

async def show_red_dot(page, x, y):
    try:
        await page.evaluate(f"""
            var dot = document.createElement('div'); dot.id = 'bot-marker';
            dot.style.position = 'absolute'; dot.style.left = '{x-15}px'; dot.style.top = '{y-15}px';
            dot.style.width = '30px'; dot.style.height = '30px'; 
            dot.style.background = 'rgba(255, 0, 0, 0.9)'; dot.style.borderRadius = '50%'; 
            dot.style.zIndex = '2147483647'; dot.style.pointerEvents = 'none'; 
            dot.style.border = '3px solid white'; dot.style.boxShadow = '0 0 10px rgba(0,0,0,0.8)';
            document.body.appendChild(dot);
            setTimeout(() => {{ if(dot) dot.remove(); }}, 2000);
        """)
    except: pass

# --- 🔥 CHINESE SOLVER WITH VISUAL DEBUG 🔥 ---
def solve_puzzle_chinese(image_path, attempt_id, slider_y_pos=None):
    try:
        with open(image_path, "rb") as i: img_bytes = i.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return 0
        
        best_x = 0
        method_used = "None"
        
        # --- LOGIC: Hybrid (Rembg + Contour + Chinese Logic) ---
        try:
            output_data = remove(img_bytes) # Remove Background
            nparr_ai = np.frombuffer(output_data, np.uint8)
            img_ai = cv2.imdecode(nparr_ai, cv2.IMREAD_UNCHANGED)
            
            if img_ai.shape[2] == 4:
                alpha = img_ai[:, :, 3]
                contours, _ = cv2.findContours(alpha, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                valid_targets = []
                
                for contour in contours:
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # 1. Shape & Size Filters
                    aspect_ratio = float(w) / h
                    is_square = 0.8 <= aspect_ratio <= 1.3
                    is_valid_size = 35 < w < 90 and 35 < h < 90
                    is_not_start = x > 60
                    
                    if is_square and is_valid_size and is_not_start:
                        # 2. Decoy Check (Y-Axis)
                        if slider_y_pos:
                            if abs(y - slider_y_pos) < 25: 
                                valid_targets.append((x, y, w, h))
                            else:
                                cv2.rectangle(img, (x, y), (x+w, y+h), (0, 0, 255), 2)
                        else:
                            valid_targets.append((x, y, w, h))
                
                if valid_targets:
                    best_target = valid_targets[0]
                    best_x = best_target[0]
                    bx, by, bw, bh = best_target
                    
                    method_used = "Chinese_AI_Lock"
                    cv2.rectangle(img, (bx, by), (bx+bw, by+bh), (255, 0, 0), 3)
                    cv2.putText(img, "TARGET", (bx, by-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

        except Exception as e:
            log_msg(f"Chinese Logic Err: {e}")

        # Save the Debug Image
        cv2.putText(img, f"Method: {method_used}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imwrite(f"{CAPTURE_DIR}/DEBUG_Try{attempt_id}_Chinese.jpg", img)
        log_msg(f"📸 Debug Image Saved: DEBUG_Try{attempt_id}_Chinese.jpg", level="step")
        
        return best_x

    except Exception as e:
        log_msg(f"Solver Error: {e}", level="step"); return 0

# --- CLICK LOGIC ---
async def execute_click_strategy(page, element, strategy_id, desc):
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if not box: return False
        cx = box['x'] + box['width'] / 2; cy = box['y'] + box['height'] / 2
        await show_red_dot(page, cx, cy)
        await capture_step(page, f"Target_{desc}", wait_time=0.2)
        await element.click()
        return True
    except: return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING, CURRENT_RETRIES
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
            res = await run_huawei_session(current_number, proxy_cfg)
            
            if res == "success":
                log_msg("🎉 Number DONE. Moving to Success.", level="main")
                save_to_file(SUCCESS_FILE, current_number)
                remove_current_number(); CURRENT_RETRIES = 0
            elif res == "captcha_fail":
                log_msg("💀 Puzzle Failed. Skip.", level="main")
                save_to_file(FAILED_FILE, current_number)
                remove_current_number(); CURRENT_RETRIES = 0
            else: 
                if CURRENT_RETRIES < 2:
                    CURRENT_RETRIES += 1
                    log_msg(f"🔁 Retrying ({CURRENT_RETRIES}/3)...", level="main")
                else:
                    log_msg("💀 Max Retries.", level="main")
                    save_to_file(FAILED_FILE, current_number)
                    remove_current_number(); CURRENT_RETRIES = 0
        except Exception as e:
            log_msg(f"🔥 Crash: {e}", level="main"); CURRENT_RETRIES += 1
        
        await asyncio.sleep(2)

async def run_huawei_session(phone, proxy):
    try:
        async with async_playwright() as p:
            launch_args = { "headless": True, "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--ignore-certificate-errors", "--window-size=1920,1080"] }
            if proxy: launch_args["proxy"] = proxy 

            log_msg("🚀 Launching Chinese AI Bot...", level="step")
            try: browser = await p.chromium.launch(**launch_args)
            except Exception as e: log_msg(f"❌ Proxy Fail: {e}", level="main"); return "retry"

            context = await browser.new_context(viewport={'width': 1920, 'height': 1080}, locale="en-US")
            await context.clear_cookies()
            page = await context.new_page()

            log_msg("🌐 Opening Huawei...", level="step")
            try:
                if not BOT_RUNNING: return "retry"
                await page.goto(BASE_URL, timeout=60000) 
                await asyncio.sleep(5) 
                
                # Reg Flow
                reg_btn = page.get_by_text("Register", exact=True).or_(page.get_by_text("Sign up", exact=True))
                if await reg_btn.count() > 0: await execute_click_strategy(page, reg_btn.first, 1, "Register_Link")
                else: log_msg("❌ Reg btn missing", level="main"); return "retry"
                await asyncio.sleep(5)

                phone_tab = page.get_by_text("Register with phone number")
                if await phone_tab.count() > 0: await execute_click_strategy(page, phone_tab.first, 1, "Phone_Tab")
                await asyncio.sleep(2)
                
                final_phone = phone
                if phone.startswith("7") and len(phone) > 10: final_phone = phone[1:] 
                phone_input = page.get_by_placeholder("Phone")
                if await phone_input.count() > 0:
                    await phone_input.click(); await page.keyboard.type(final_phone, delay=100)
                else: return "retry"

                # Get Code
                get_code_btn = page.get_by_text("Get code", exact=True)
                if await get_code_btn.count() > 0:
                    await execute_click_strategy(page, get_code_btn.first, 1, "Get_Code_Btn")
                    log_msg("⏳ Hard Wait: 10s for Initial Load...", level="step")
                    await asyncio.sleep(10)
                    
                    attempt_count = 0
                    while attempt_count < 5:
                        if not BOT_RUNNING: return "retry"
                        
                        puzzle_container = page.locator(".geetest_window").or_(page.locator(".nc_scale")).or_(page.locator("iframe[src*='captcha']"))
                        if await puzzle_container.count() > 0 or await page.get_by_text("Please complete verification").count() > 0:
                            attempt_count += 1
                            log_msg(f"🧩 Captcha Found! Attempt {attempt_count}...", level="main")
                            
                            puzzle_img = page.locator("img[src*='captcha']").first
                            if await puzzle_img.count() == 0: puzzle_img = page.locator(".geetest_canvas_bg").first
                            
                            if await puzzle_img.count() > 0:
                                await capture_step(page, f"Try_{attempt_count}_Start")
                                await puzzle_img.screenshot(path="temp_puzzle.png")
                                
                                # 🔥 GET SLIDER Y POSITION 🔥
                                slider_y_relative = 0
                                slider = page.locator(".geetest_slider_button").or_(page.locator(".nc_iconfont.btn_slide")).or_(page.locator(".yidun_slider"))
                                if await slider.count() > 0:
                                    slider_box = await slider.bounding_box()
                                    img_box = await puzzle_img.bounding_box()
                                    if slider_box and img_box:
                                        slider_center_y = slider_box['y'] + (slider_box['height'] / 2)
                                        img_top_y = img_box['y']
                                        slider_y_relative = slider_center_y - img_top_y

                                # Scale Calc
                                box_img = await puzzle_img.bounding_box()
                                actual_width = box_img['width']
                                temp_img_cv = cv2.imread("temp_puzzle.png")
                                raw_width = temp_img_cv.shape[1]
                                scale_ratio = actual_width / raw_width
                                
                                # 🔥 SOLVE 🔥
                                raw_slider_y = slider_y_relative / scale_ratio if slider_y_relative > 0 else None
                                distance_raw = solve_puzzle_chinese("temp_puzzle.png", attempt_count, raw_slider_y)
                                
                                if distance_raw > 0:
                                    distance = distance_raw * scale_ratio
                                    log_msg(f"🧠 AI Target: {distance:.2f}px", level="step")
                                    
                                    if await slider.count() > 0:
                                        box = await slider.bounding_box()
                                        start_x = box['x'] + box['width'] / 2; start_y = box['y'] + box['height'] / 2
                                        await page.mouse.move(start_x, start_y); await page.mouse.down()
                                        
                                        target_x = start_x + distance
                                        steps = 15
                                        for i in range(steps):
                                            move_x = start_x + (distance * (i / steps))
                                            move_y = start_y + random.randint(-5, 5) 
                                            await page.mouse.move(move_x, move_y)
                                            if i == 7: await show_red_dot(page, move_x, move_y)
                                            await asyncio.sleep(0.02)

                                        await show_red_dot(page, target_x, start_y)
                                        await page.mouse.move(target_x, start_y); await asyncio.sleep(0.5); await page.mouse.up()
                                        
                                        log_msg("🚀 Dropped! Verify Wait 10s...", level="step")
                                        await asyncio.sleep(10)
                                        continue 
                                else:
                                    log_msg("❌ AI found 0px target.", level="step")
                        else:
                            if await page.get_by_text("s", exact=False).count() > 0 or await page.get_by_text("sent", exact=False).count() > 0:
                                log_msg("✅ SUCCESS! Code Sent.", level="main")
                                await capture_step(page, "Success_Final")
                                return "success"
                            else:
                                log_msg("❌ No Captcha & No Success.", level="main")
                                return "captcha_fail"
                    return "captcha_fail"
                else: return "retry"
            except Exception as e:
                log_msg(f"❌ Session Error: {str(e)}", level="main"); return "retry"
            finally:
                await browser.close()
                if os.path.exists("temp_puzzle.png"): os.remove("temp_puzzle.png")
    except Exception as launch_e:
        log_msg(f"❌ LAUNCH ERROR: {launch_e}", level="main"); return "retry"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")