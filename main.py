import os
import asyncio
import random
import time
import shutil
from urllib.parse import urlparse
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
NUMBERS_FILE = "numbers.txt"
PROXY_FILE = "proxies.txt"
BASE_URL = "https://id5.cloud.huawei.com"
CONCURRENT_WORKERS = 10  # 🔥 10 Parallel Processes

app = FastAPI()

# --- IMPORT SOLVER ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    print("❌ ERROR: captcha_solver.py not found!")
    async def solve_captcha(page, session_id): return False

# --- GLOBAL SETTINGS ---
SETTINGS = {
    "country": "Russia",
    "proxy_manual": "",
}

# --- GLOBAL STATE ---
BOT_RUNNING = False
NUMBER_QUEUE = asyncio.Queue()

# --- PROXY UTILS ---
def parse_proxy(proxy_str):
    if not proxy_str or len(proxy_str) < 5: return None
    p = proxy_str.strip()
    if "://" not in p: p = f"http://{p}"
    try:
        u = urlparse(p)
        cfg = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
        if u.username and u.password:
            cfg["username"] = u.username
            cfg["password"] = u.password
        return cfg
    except: return None

def get_proxy():
    if SETTINGS["proxy_manual"]: return parse_proxy(SETTINGS["proxy_manual"])
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines: return parse_proxy(random.choice(lines))
        except: pass
    return None

# --- API ENDPOINTS ---
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <html>
    <body style="background:#111; color:#0f0; font-family:monospace; text-align:center; padding:50px;">
        <h1>🚀 HUAWEI TURBO BOT (10x)</h1>
        <button onclick="fetch('/start', {method:'POST'})" style="padding:15px; background:blue; color:white; border:none; cursor:pointer;">START WORKERS</button>
        <button onclick="fetch('/stop', {method:'POST'})" style="padding:15px; background:red; color:white; border:none; cursor:pointer;">STOP ALL</button>
        <p>Check terminal for logs.</p>
    </body>
    </html>
    """

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
    return {"status": "saved"}

@app.post("/start")
async def start_bot(bt: BackgroundTasks):
    global BOT_RUNNING
    if not BOT_RUNNING:
        BOT_RUNNING = True
        # Load numbers into Queue
        if os.path.exists(NUMBERS_FILE):
            with open(NUMBERS_FILE, "r") as f:
                nums = [l.strip() for l in f.readlines() if l.strip()]
            for n in nums: NUMBER_QUEUE.put_nowait(n)
            print(f"🔥 Loaded {NUMBER_QUEUE.qsize()} numbers. Starting {CONCURRENT_WORKERS} workers...")
            
            # Start Workers
            for i in range(CONCURRENT_WORKERS):
                bt.add_task(worker_loop, i)
        else:
            print("⚠️ No numbers.txt found!")
    return {"status": "started"}

@app.post("/stop")
async def stop_bot():
    global BOT_RUNNING
    BOT_RUNNING = False
    return {"status": "stopping"}

# --- WORKER LOGIC ---
async def worker_loop(worker_id):
    while BOT_RUNNING and not NUMBER_QUEUE.empty():
        try:
            number = NUMBER_QUEUE.get_nowait()
            print(f"Processing {number}...")
            await process_number(number)
        except asyncio.QueueEmpty:
            break
        except Exception as e:
            print(f"Worker Error: {e}")

async def process_number(phone_number):
    proxy = get_proxy()
    target_country = SETTINGS["country"]
    
    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        }
        if proxy: launch_args["proxy"] = proxy

        try:
            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={'width': 412, 'height': 950},
                user_agent="Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
            )
            page = await context.new_page()

            # --- NAVIGATION ---
            try:
                await page.goto(BASE_URL, timeout=60000)
                
                # 1. Register
                reg = page.get_by_text("Register", exact=True).or_(page.get_by_role("button", name="Register"))
                await reg.first.click(); await asyncio.sleep(2)

                # 2. Agree (Tick Box Trick)
                cb_text = page.get_by_text("stay informed", exact=False).first
                if await cb_text.count() > 0: await cb_text.click()
                
                agree = page.get_by_text("Agree", exact=True).or_(page.get_by_text("Next", exact=True))
                await agree.first.click(); await asyncio.sleep(2)

                # 3. DOB
                # Scroll first
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                
                dob_next = page.get_by_text("Next", exact=True)
                await dob_next.first.click(); await asyncio.sleep(2)

                # 4. Phone Option
                use_phone = page.get_by_text("Use phone number", exact=False)
                await use_phone.first.click(); await asyncio.sleep(2)

                # 5. Country Switch
                # Try Arrow first, then Label
                arrow = page.locator(".hwid-list-item-arrow").first
                if await arrow.count() > 0: await arrow.click()
                else: 
                    # Right side click fallback
                    await page.touchscreen.tap(370, 150) # Approx coords
                
                # Wait for search box
                try:
                    search = page.get_by_placeholder("Search", exact=False).first
                    await search.wait_for(timeout=5000)
                    await search.click()
                    await page.keyboard.type(target_country, delay=20)
                    await asyncio.sleep(2)
                    
                    matches = page.get_by_text(target_country, exact=False)
                    if await matches.count() > 1: await matches.nth(1).click()
                    else: await matches.first.click()
                    await asyncio.sleep(2)
                except:
                    # Maybe country list didn't open, proceed if possible or retry logic could be here
                    pass

                # 6. Input Number
                inp = page.locator("input[type='tel']").first
                await inp.click()
                await page.keyboard.type(phone_number, delay=20)
                await page.touchscreen.tap(350, 100) # Hide KB
                
                get_code = page.locator(".get-code-btn").or_(page.get_by_text("Get code")).first
                await get_code.click()
                await asyncio.sleep(2)

                # 🔥 CHECK FOR ERROR POPUP 🔥
                err_popup = page.get_by_text("An unexpected problem", exact=False)
                if await err_popup.count() > 0:
                    print(f"Error: {phone_number} Not Supported. Skipping.")
                    await browser.close()
                    return # Exit function

                # 7. Captcha Logic
                start_time = time.time()
                while time.time() - start_time < 60:
                    # Check for captcha
                    captcha_frame = None
                    for frame in page.frames:
                        try:
                            if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                captcha_frame = frame; break
                        except: pass
                    
                    if captcha_frame:
                        print(f"Solving Captcha for {phone_number}...")
                        await asyncio.sleep(5) # Allow load
                        
                        # Call Solver
                        session_id = f"sess_{random.randint(1000,9999)}"
                        solved = await solve_captcha(page, session_id)
                        
                        if not solved:
                            await browser.close(); return # Fail silently on solver error
                        
                        await asyncio.sleep(5) # Wait for result
                        
                        # Verify Success
                        is_still_there = False
                        for frame in page.frames:
                            try:
                                if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                    is_still_there = True; break
                            except: pass
                        
                        if not is_still_there:
                            print(f"Success: {phone_number} Verified!")
                            await browser.close()
                            return
                        else:
                            # Retry loop
                            await asyncio.sleep(2)
                            continue
                    
                    await asyncio.sleep(1)

            except Exception:
                # Silent fail on navigation errors to keep logs clean
                pass
            
            await browser.close()

        except Exception:
            pass