import os
import glob
import asyncio
import random
import cv2  # 🔥 OpenCV
import numpy as np
from rembg import remove
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
import uvicorn

# --- 🔥 CONFIGURATION 🔥 ---
CAPTURE_DIR = "./captures"
NUMBERS_FILE = "numbers.txt"
SUCCESS_FILE = "success.txt"
FAILED_FILE = "failed.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id8.cloud.huawei.com/" 

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

SETTINGS = {"country": "Default", "proxy_manual": ""}
BOT_RUNNING = False
logs = []
CURRENT_RETRIES = 0 
PROXY_INDEX = 0

# --- HELPERS & API ---
def log_msg(message, level="step"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry)
    logs.insert(0, entry)
    if len(logs) > 500: logs.pop()

def get_current_number_from_file():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: lines = [l.strip() for l in f.readlines() if l.strip()]
        if lines: return lines[0]
    return None

def remove_current_number():
    if os.path.exists(NUMBERS_FILE):
        with open(NUMBERS_FILE, "r") as f: lines = f.readlines()
        if lines:
            with open(NUMBERS_FILE, "w") as f: f.writelines(lines[1:])

def save_to_file(filename, data):
    with open(filename, "a") as f: f.write(f"{data}\n")

def get_current_proxy():
    global PROXY_INDEX
    if SETTINGS["proxy_manual"] and len(SETTINGS["proxy_manual"]) > 5:
        parts = SETTINGS["proxy_manual"].strip().split(":")
        if len(parts) >= 2: return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]} if len(parts) == 4 else {"server": f"http://{parts[0]}:{parts[1]}"}
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f: lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                if PROXY_INDEX >= len(lines): PROXY_INDEX = 0
                sel = lines[PROXY_INDEX]; PROXY_INDEX += 1
                parts = sel.split(":")
                return {"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]} if len(parts) == 4 else {"server": f"http://{parts[0]}:{parts[1]}"}
        except: pass
    return None

@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    # Sort files by time to show newest debug images first
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg'), key=os.path.getmtime, reverse=True)[:15]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    return JSONResponse({"logs": logs[:50], "images": images, "running": BOT_RUNNING})

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING: BOT_RUNNING = True; bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING; BOT_RUNNING = False; return {"status": "stopping"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "saved"}

# --- VISUALS ---
async def capture_step(page, step_name):
    if not BOT_RUNNING: return
    ts = datetime.now().strftime("%H%M%S")
    fn = f"{CAPTURE_DIR}/{ts}_{step_name}.jpg"
    try: await page.screenshot(path=fn)
    except: pass

async def show_red_dot(page, x, y):
    try:
        await page.evaluate(f"""
            var dot = document.createElement('div'); dot.style.position='absolute';
            dot.style.left='{x-10}px'; dot.style.top='{y-10}px'; dot.style.width='20px'; dot.style.height='20px';
            dot.style.background='red'; dot.style.borderRadius='50%'; dot.style.zIndex='9999';
            document.body.appendChild(dot); setTimeout(()=>dot.remove(), 2000);
        """)
    except: pass

# --- 🔥 HEAVY DEBUG SOLVER (DRAWS BOXES) 🔥 ---
def solve_puzzle_with_debug(image_path, attempt_id):
    try:
        # Load Image
        with open(image_path, "rb") as i: img_bytes = i.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return 0
        
        best_x = 0
        
        # --- PRE-PROCESSING (Reduce Noise) ---
        # Huawei images are noisy. We need strong blur.
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Method: Enhance Contrast to make the hole darker
        alpha_c = 1.5 # Contrast control
        beta_c = 0    # Brightness control
        enhanced = cv2.convertScaleAbs(gray, alpha=alpha_c, beta=beta_c)
        
        # Gaussian Blur
        blurred = cv2.GaussianBlur(enhanced, (7, 7), 0)
        
        # Canny Edge
        edges = cv2.Canny(blurred, 50, 150)
        
        # Find Contours
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        found_contours = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            # Filter: Huawei holes are roughly square, size 40-50px usually
            # We allow 35-85px range. Must not be at x=0 (that's the slider piece)
            if 35 < w < 85 and 35 < h < 85 and x > 60:
                found_contours.append((x, y, w, h))
        
        # Sort contours by X (Left to Right) - Usually first valid one is the target
        found_contours.sort(key=lambda b: b[0])
        
        if found_contours:
            # Pick the best one
            target = found_contours[0]
            best_x = target[0]
            
            # 🔥 DRAW DEBUG BOX 🔥
            # Draw rectangle on original image
            cv2.rectangle(img, (target[0], target[1]), (target[0]+target[2], target[1]+target[3]), (0, 255, 0), 2)
            # Save Debug Image
            cv2.imwrite(f"{CAPTURE_DIR}/DEBUG_{attempt_id}_Solved.jpg", img)
            log_msg(f"📸 Debug Image Saved: DEBUG_{attempt_id}_Solved.jpg", level="step")
        else:
            log_msg("❌ No valid contours found.", level="step")
            cv2.imwrite(f"{CAPTURE_DIR}/DEBUG_{attempt_id}_FAILED.jpg", edges) # Save edge view to see why

        return best_x

    except Exception as e:
        log_msg(f"Solver Error: {e}", level="step")
        return 0

# --- CLICK ---
async def click_element(page, elem, desc):
    try:
        box = await elem.bounding_box()
        if box:
            await show_red_dot(page, box['x']+box['width']/2, box['y']+box['height']/2)
            await capture_step(page, f"Click_{desc}")
            await elem.click()
            return True
    except: pass
    return False

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING, CURRENT_RETRIES
    if not get_current_number_from_file(): log_msg("ℹ️ No Numbers."); BOT_RUNNING = False; return
    
    while BOT_RUNNING:
        num = get_current_number_from_file()
        if not num: break
        
        proxy = get_current_proxy()
        log_msg(f"🔵 Processing: {num}", level="main")
        
        try:
            res = await run_session(num, proxy)
            if res == "success":
                save_to_file(SUCCESS_FILE, num); remove_current_number(); CURRENT_RETRIES = 0
            elif res == "captcha_fail":
                save_to_file(FAILED_FILE, num); remove_current_number(); CURRENT_RETRIES = 0
            else:
                if CURRENT_RETRIES < 2: CURRENT_RETRIES += 1
                else: save_to_file(FAILED_FILE, num); remove_current_number(); CURRENT_RETRIES = 0
        except Exception as e:
            log_msg(f"Error: {e}"); CURRENT_RETRIES += 1
        
        await asyncio.sleep(2)

async def run_session(phone, proxy):
    try:
        async with async_playwright() as p:
            args = ["--no-sandbox", "--disable-blink-features=AutomationControlled", "--window-size=1920,1080"]
            browser = await p.chromium.launch(headless=True, args=args, proxy=proxy)
            context = await browser.new_context(viewport={'width':1920,'height':1080}, locale="en-US")
            page = await context.new_page()
            
            try:
                log_msg("🌐 Opening Page...", level="step")
                await page.goto(BASE_URL, timeout=60000)
                await asyncio.sleep(5)
                
                # Navigation
                if await page.get_by_text("Register").count() > 0:
                    await click_element(page, page.get_by_text("Register").first, "Reg")
                await asyncio.sleep(3)
                
                if await page.get_by_text("Register with phone").count() > 0:
                    await click_element(page, page.get_by_text("Register with phone").first, "Tab")
                
                # Input
                clean_phone = phone[1:] if phone.startswith("7") else phone
                await page.get_by_placeholder("Phone").fill(clean_phone)
                
                # Get Code
                await click_element(page, page.get_by_text("Get code").first, "GetCode")
                
                # 🔥 CAPTCHA LOOP 🔥
                log_msg("⏳ Waiting 10s for Captcha...", level="step")
                await asyncio.sleep(10)
                
                for attempt in range(1, 6): # 5 Attempts
                    if not BOT_RUNNING: return "retry"
                    
                    # Check if Captcha is present
                    captcha_frame = page.locator("iframe[src*='captcha']").or_(page.locator(".geetest_window"))
                    is_present = await captcha_frame.count() > 0 or await page.get_by_text("verification").count() > 0
                    
                    if is_present:
                        log_msg(f"🧩 Captcha Detected (Try {attempt})...", level="main")
                        
                        # Find Image
                        img_elem = page.locator("img[src*='captcha']").first
                        if await img_elem.count() == 0: img_elem = page.locator(".geetest_canvas_bg").first
                        
                        if await img_elem.count() > 0:
                            # Screenshot for OpenCV
                            await img_elem.screenshot(path="puzzle.png")
                            
                            # Scaling Calculation
                            box = await img_elem.bounding_box()
                            display_width = box['width']
                            raw_img = cv2.imread("puzzle.png")
                            raw_width = raw_img.shape[1]
                            scale = display_width / raw_width
                            
                            # 🔥 SOLVE & SAVE DEBUG IMAGE 🔥
                            raw_distance = solve_puzzle_with_debug("puzzle.png", attempt)
                            
                            if raw_distance > 0:
                                distance = raw_distance * scale
                                log_msg(f"📏 Distance: {distance}px (Raw: {raw_distance})", level="step")
                                
                                # Drag
                                slider = page.locator(".geetest_slider_button").or_(page.locator(".nc_iconfont.btn_slide"))
                                if await slider.count() > 0:
                                    s_box = await slider.bounding_box()
                                    sx = s_box['x'] + s_box['width']/2
                                    sy = s_box['y'] + s_box['height']/2
                                    
                                    await page.mouse.move(sx, sy); await page.mouse.down()
                                    
                                    # Human Move
                                    await page.mouse.move(sx + distance, sy + random.randint(-5,5), steps=15)
                                    await asyncio.sleep(0.5); await page.mouse.up()
                                    
                                    log_msg("🚀 Dropped! Waiting 10s...", level="step")
                                    await asyncio.sleep(10)
                                    continue
                            else:
                                log_msg("❌ AI failed to find hole.", level="step")
                        else:
                            log_msg("❌ Image element missing.", level="step")
                    else:
                        # Check Success
                        if await page.get_by_text("s", exact=False).count() > 0:
                            log_msg("✅ SUCCESS!", level="main"); return "success"
                        else:
                            log_msg("❌ Failed/Error.", level="main"); return "captcha_fail"
                
                return "captcha_fail"

            except Exception as e:
                log_msg(f"Session Err: {e}"); return "retry"
            finally:
                await browser.close()

    except: return "retry"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")