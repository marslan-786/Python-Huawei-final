import os
import glob
import asyncio
import random
import shutil
import base64
import json
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
import uvicorn
from PIL import Image
import cv2
import numpy as np

# --- 🧠 AI CLIENTS SETUP ---
AI_LIBS_INSTALLED = False
try:
    from google import genai
    from groq import Groq
    AI_LIBS_INSTALLED = True
except ImportError as e:
    print(f"❌ CRITICAL: Libraries missing! Run: pip install google-genai groq")

# 🔑 API KEYS
KEY_GEMINI = "AIzaSyD2kBM01JsV1GEYPFbo6U0iayd49bxASo0"
KEY_GROQ = "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"

# Init Clients
client_gemini = None
client_groq = None

try:
    if AI_LIBS_INSTALLED:
        client_gemini = genai.Client(api_key=KEY_GEMINI)
        client_groq = Groq(api_key=KEY_GROQ)
except Exception as e:
    print(f"❌ Client Init Error: {e}")

# --- CONFIG ---
BASE_URL = "https://id8.cloud.huawei.com/CAS/portal/login.html"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(BASE_DIR, "captures")
NUMBERS_FILE = os.path.join(BASE_DIR, "numbers.txt")
SUCCESS_FILE = os.path.join(BASE_DIR, "success.txt")
FAILED_FILE = os.path.join(BASE_DIR, "failed.txt")
PROXY_FILE = os.path.join(BASE_DIR, "proxies.txt")

app = FastAPI()
if not os.path.exists(CAPTURE_DIR): os.makedirs(CAPTURE_DIR)
app.mount("/captures", StaticFiles(directory=CAPTURE_DIR), name="captures")

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
        with open(NUMBERS_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        with open(NUMBERS_FILE, "w", encoding="utf-8") as f: f.writelines(lines[1:])
    except: pass

def save_to_file(filename, data):
    with open(filename, "a", encoding="utf-8") as f: f.write(f"{data}\n")

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# --- PROXY LOGIC ---
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

# --- VISUALS ---
async def capture_step(page, step_name):
    if not BOT_RUNNING: return
    ts = datetime.now().strftime("%H%M%S")
    filename = f"{CAPTURE_DIR}/{ts}_{step_name}.jpg"
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

# --- 🧠 MULTI-AI BRAIN (PERCENTAGE BASED) ---
def call_all_ais(image_path, attempt_num):
    log_msg(f"📡 AI Round {attempt_num} Requesting...", level="step")
    
    # 🔥 Updated Prompt for Percentage (0.0 to 1.0)
    prompt_text = """
    Analyze this captcha image. It has a puzzle piece (slider) and a target hole.
    Calculate the horizontal distance from the center of the slider to the center of the target hole.
    
    CRITICAL: Return the distance as a RATIO of the total image width (from 0.0 to 1.0).
    Example: If the image width is 300px and distance is 150px, return 0.5.
    
    Return ONLY a JSON object:
    {"distance_ratio": 0.45}
    Do not explain. Just JSON.
    """
    
    results = {"Gemini": None, "Groq": None}
    
    # 1. Gemini
    try:
        img_pil = Image.open(image_path)
        resp = client_gemini.models.generate_content(
            model="gemini-2.5-flash", # Stable
            contents=[prompt_text, img_pil]
        )
        if resp.text:
            clean_text = resp.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            results["Gemini"] = data.get('distance_ratio')
            log_msg(f"✅ Gemini Ratio: {results['Gemini']}", level="step")
    except Exception as e:
        log_msg(f"❌ Gemini Error: {e}", level="step")

    # 2. Groq (Backup)
    try:
        if results["Gemini"] is None:
            img_b64 = encode_image(image_path)
            resp = client_groq.chat.completions.create(
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}],
                model="llama-3.2-11b-vision-preview" 
            )
            raw_groq = resp.choices[0].message.content
            data = json.loads(raw_groq.replace("```json", "").replace("```", "").strip())
            results["Groq"] = data.get('distance_ratio')
            log_msg(f"✅ Groq Ratio: {results['Groq']}", level="step")
    except Exception as e:
        pass
    
    return results

# --- HUMAN MOUSE MOVEMENT ---
async def human_drag(page, start_x, start_y, end_x, end_y):
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()
    
    # Calculate distance
    distance = end_x - start_x
    steps = 25 # Increase steps for smoother "human" look
    
    for i in range(steps):
        # Ease-out function (starts fast, slows down at end)
        progress = i / steps
        ease = 1 - (1 - progress) * (1 - progress) 
        
        current_x = start_x + (distance * ease)
        # Add tiny random Y jitter (shake hand slightly)
        jitter_y = start_y + random.uniform(-2, 2)
        
        await page.mouse.move(current_x, jitter_y)
        await asyncio.sleep(random.uniform(0.01, 0.03)) # Random tiny delays

    # Final Adjustment
    await page.mouse.move(end_x, end_y)
    await asyncio.sleep(0.1)
    await page.mouse.up()

# --- WORKER ---
async def master_loop():
    global BOT_RUNNING
    log_msg("🚀 SYSTEM STARTED: Percentage + Human Drag", level="main")
    
    while BOT_RUNNING:
        current_number = get_current_number_from_file()
        if not current_number:
            log_msg("🏁 No numbers left. Stopping.", level="main"); BOT_RUNNING = False; break

        proxy_cfg = get_current_proxy()
        p_show = proxy_cfg['server'] if proxy_cfg else "🌐 Direct"
        log_msg(f"📞 {current_number} | {p_show}", level="main")

        try:
            async with async_playwright() as p:
                launch_args = { "headless": True, "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"] }
                if proxy_cfg: launch_args["proxy"] = proxy_cfg
                
                browser = await p.chromium.launch(**launch_args)
                context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
                page = await context.new_page()

                log_msg("🌍 Opening ID8...", level="step")
                try:
                    await page.goto(BASE_URL, timeout=60000)
                    log_msg("⏳ Waiting 5s for Page Load...", level="step")
                    await asyncio.sleep(5)
                    await capture_step(page, "1_Page_Loaded")
                except:
                    log_msg("💀 Page Load Timeout.", level="main"); await browser.close(); continue

                # 2. CLICK REGISTER
                if await page.get_by_text("Register HUAWEI ID", exact=True).count() == 0:
                    reg_btn = page.get_by_text("Register", exact=True).or_(page.get_by_text("Sign up", exact=True))
                    if await reg_btn.count() > 0:
                        log_msg("🖱️ Clicking Register Link...", level="step")
                        await reg_btn.first.click()
                        await capture_step(page, "2_Register_Clicked")
                        log_msg("⏳ Waiting 3s after Register click...", level="step")
                        await asyncio.sleep(3)
                    else:
                        log_msg("✅ Probably already on Register Page.", level="step")
                else:
                    log_msg("✅ Already on Register Page.", level="step")

                # 3. PHONE INPUT
                phone_input = page.get_by_placeholder("Phone")
                if not await phone_input.is_visible():
                    log_msg("🖱️ Clicking Phone Tab...", level="step")
                    phone_tab = page.get_by_text("Register with phone number")
                    if await phone_tab.count() > 0:
                        await phone_tab.first.click()
                        await asyncio.sleep(2)
                        await capture_step(page, "3_Phone_Tab_Clicked")
                
                if await phone_input.count() > 0:
                    log_msg("⌨️ Typing Number...", level="step")
                    clean_phone = current_number.replace("+", "").replace(" ", "")
                    if clean_phone.startswith("7") and len(clean_phone) > 10: clean_phone = clean_phone[1:]
                    await phone_input.click()
                    await page.keyboard.type(clean_phone, delay=100)
                    await asyncio.sleep(1)
                    await capture_step(page, "4_Number_Typed")
                else:
                    log_msg("💀 Phone Input Missing!", level="main")
                    await capture_step(page, "Error_No_Input")
                    await browser.close(); continue

                # 5. CLICK GET CODE
                code_btn = page.get_by_text("Get code", exact=True)
                if await code_btn.count() > 0:
                    log_msg("🖱️ Clicking Get Code...", level="step")
                    await code_btn.first.click()
                    log_msg("⏳ Hard Wait: 10s for Captcha...", level="step")
                    await asyncio.sleep(10)
                    await capture_step(page, "5_After_Get_Code")
                else:
                    log_msg("💀 Get Code Button Missing!", level="main")
                    await capture_step(page, "Error_No_GetCode")
                    await browser.close(); continue

                # --- 🧩 CAPTCHA LOGIC ---
                captcha_solved = False
                
                for attempt in range(3):
                    log_msg(f"⚔️ Round {attempt+1}: Gemini", level="main")
                    
                    puzzle = page.locator("img[src*='captcha']").first
                    if await puzzle.count() == 0: puzzle = page.locator(".geetest_canvas_bg").first
                    
                    if await puzzle.count() > 0:
                        ts = datetime.now().strftime("%H%M%S")
                        sname = f"{CAPTURE_DIR}/try_{ts}_{attempt}.png"
                        await puzzle.screenshot(path=sname)
                        
                        # Call AI for Ratio (0.0 - 1.0)
                        all_res = call_all_ais(sname, attempt+1)
                        ratio = all_res.get("Gemini") or all_res.get("Groq")
                        
                        if ratio:
                            # Calculate Pixels based on ACTUAL Browser Element Width
                            box = await puzzle.bounding_box()
                            actual_width = box['width']
                            move_px = ratio * actual_width
                            
                            log_msg(f"🤖 Move Ratio: {ratio} | Px: {move_px:.2f}", level="step")
                            
                            slider = page.locator(".geetest_slider_button").or_(page.locator(".nc_iconfont.btn_slide")).or_(page.locator(".yidun_slider"))
                            if await slider.count() > 0:
                                s_box = await slider.bounding_box()
                                sx, sy = s_box['x'] + s_box['width']/2, s_box['y'] + s_box['height']/2
                                
                                # 🔥 HUMAN DRAG
                                await human_drag(page, sx, sy, sx + move_px, sy)
                                
                                log_msg("⏳ Verifying (10s)...", level="step")
                                await asyncio.sleep(10)
                                await capture_step(page, f"6_Verify_Round_{attempt+1}")
                                
                                if await puzzle.count() == 0 or not await puzzle.is_visible():
                                    log_msg("🎉 CAPTCHA SOLVED!", level="main")
                                    captcha_solved = True; break
                            else: log_msg("❌ Slider Missing", level="step")
                        else: log_msg(f"❌ Gemini returned Null", level="step")
                    else:
                        log_msg("ℹ️ No Captcha Found (Maybe Skipped)", level="step")
                        captcha_solved = True; break

                if not captcha_solved:
                    log_msg("🔥🔥 ALL AI FAILED. KILL SWITCH.", level="main")
                    await capture_step(page, "Error_Final_Fail")
                    BOT_RUNNING = False
                    save_to_file(FAILED_FILE, current_number)
                elif await page.get_by_text("sent", exact=False).count() > 0:
                    log_msg("✅ SMS SENT!", level="main")
                    await capture_step(page, "7_Success")
                    save_to_file(SUCCESS_FILE, current_number)
                    remove_current_number()
                else:
                    log_msg("⚠️ Unknown State", level="step")
                    await capture_step(page, "8_Unknown")
                    remove_current_number()

                await browser.close()

        except Exception as e:
            log_msg(f"🔥 CRASH: {e}", level="main"); await asyncio.sleep(5)

# --- WEB ROUTES ---
@app.get("/")
async def read_index(): return FileResponse('index.html')

@app.get("/status")
async def get_status():
    files = sorted(glob.glob(f'{CAPTURE_DIR}/*.jpg') + glob.glob(f'{CAPTURE_DIR}/*.png'), key=os.path.getmtime, reverse=True)[:15]
    images = [f"/captures/{os.path.basename(f)}" for f in files]
    stats = { "remaining": count_file_lines(NUMBERS_FILE), "success": count_file_lines(SUCCESS_FILE), "failed": count_file_lines(FAILED_FILE) }
    return JSONResponse({"logs": logs[:50], "images": images, "running": BOT_RUNNING, "stats": stats})

@app.post("/update_settings")
async def update_settings(country: str = Form(...), manual_proxy: Optional[str] = Form("")):
    SETTINGS["country"] = country; SETTINGS["proxy_manual"] = manual_proxy
    return {"status": "updated"}

@app.post("/upload_numbers")
async def upload_numbers(file: UploadFile = File(...)):
    with open(NUMBERS_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "saved", "count": count_file_lines(NUMBERS_FILE)}

@app.post("/upload_proxies")
async def upload_proxies(file: UploadFile = File(...)):
    with open(PROXY_FILE, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "saved", "count": count_file_lines(PROXY_FILE)}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING: BOT_RUNNING = True; bt.add_task(master_loop)
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING; BOT_RUNNING = False
    return {"status": "stopping"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)