import os
import sys
import asyncio
import argparse
import time
from datetime import datetime
from playwright.async_api import async_playwright

# --- FORCE UNBUFFERED OUTPUT ---
sys.stdout.reconfigure(line_buffering=True)

# --- IMPORT SOLVER ---
try:
    from captcha_solver import solve_captcha
except ImportError:
    print("[ERROR] captcha_solver.py not found!", flush=True)
    sys.exit(1)

# --- CONFIG ---
BASE_URL = "https://id5.cloud.huawei.com"
CAPTURE_DIR = "./captures"

if not os.path.exists(CAPTURE_DIR):
    os.makedirs(CAPTURE_DIR)

# --- HELPER FUNCTIONS ---
def log_to_go(msg_type, content):
    print(f"[{msg_type}] {content}", flush=True)

async def single_capture(page, step_name):
    ts = datetime.now().strftime("%H%M%S")
    try:
        path = f"{CAPTURE_DIR}/{ts}_{step_name}.jpg"
        await page.screenshot(path=path)
    except: pass

async def step_wait(page, seconds, step_name):
    log_to_go("LOG", f"⏳ Waiting {seconds}s ({step_name})...")
    await asyncio.sleep(seconds)
    try: await page.wait_for_load_state("domcontentloaded", timeout=2000)
    except: pass
    await single_capture(page, step_name)

async def visual_tap(page, element, desc):
    """
    NUCLEAR TAP LOGIC: Tries standard tap, then JS Click.
    """
    try:
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            
            # Red Dot
            await page.evaluate(f"""
                var dot = document.createElement('div');
                dot.style.position = 'absolute'; 
                dot.style.left = '{x}px'; dot.style.top = '{y}px';
                dot.style.width = '20px'; dot.style.height = '20px'; 
                dot.style.background = 'rgba(255,0,0,0.7)';
                dot.style.borderRadius = '50%'; 
                dot.style.zIndex = '999999'; 
                document.body.appendChild(dot);
            """)
            
            log_to_go("LOG", f"👆 Tapping '{desc}'...")
            
            # 1. Standard Touch
            await page.touchscreen.tap(x, y)
            await asyncio.sleep(0.2)
            
            # 2. FORCE JS CLICK (The fix for stuck buttons)
            await element.evaluate("e => e.click()")
            
            return True
    except Exception as e:
        log_to_go("LOG", f"⚠️ Tap Error: {e}")
    return False

# --- STRICT NAVIGATOR ---
async def strict_navigator(page, click_selector, next_selectors, step_name):
    retries = 0
    while retries < 3:
        # Try to handle Cookie Banners first
        try:
            cookie_btn = page.get_by_text("Accept", exact=True).first
            if await cookie_btn.count() > 0 and await cookie_btn.is_visible():
                await cookie_btn.click()
                await asyncio.sleep(1)
        except: pass

        btn = page.get_by_text(click_selector, exact=True).first
        if await btn.count() == 0: btn = page.get_by_text(click_selector, exact=False).first
        if await btn.count() == 0: btn = page.get_by_role("button", name=click_selector).first
        
        if await btn.count() > 0:
            if await visual_tap(page, btn, step_name):
                # Monitor for success
                for i in range(15):
                    for sel in next_selectors:
                        if await page.get_by_text(sel).count() > 0:
                            log_to_go("LOG", f"✅ Next Page Found: {sel}")
                            await single_capture(page, f"{step_name}_Success")
                            return True
                    await asyncio.sleep(1)
                
                log_to_go("LOG", f"❌ Timeout after {step_name}. Retrying...")
                await single_capture(page, f"{step_name}_Stuck")
                retries += 1
                await page.reload()
                await asyncio.sleep(4)
        else:
            log_to_go("LOG", f"❌ Button '{click_selector}' not found.")
            await asyncio.sleep(2)
            retries += 1
            
    return False

# --- MAIN LOGIC ---
async def run_session(phone_number, country, proxy_str):
    
    launch_args = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    }

    if proxy_str and proxy_str.lower() != "none":
        parts = proxy_str.split(':')
        if "http" not in proxy_str and len(parts) == 4:
            launch_args["proxy"] = {
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2], "password": parts[3]
            }
        else:
            launch_args["proxy"] = {"server": proxy_str}
        log_to_go("LOG", f"Using Proxy: {proxy_str[:15]}...")

    async with async_playwright() as p:
        pixel_5 = p.devices['Pixel 5'].copy()
        pixel_5['user_agent'] = "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36"
        pixel_5['viewport'] = {'width': 412, 'height': 950}

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(**pixel_5, locale="en-US")
        page = await context.new_page()

        try:
            log_to_go("LOG", "🌍 Loading Huawei...")
            await page.goto(BASE_URL, timeout=60000)
            await step_wait(page, 4, "01_Load")

            # --- 1. REGISTER (With Cookie Fix) ---
            if not await strict_navigator(page, "Register", ["Agree", "Next"], "Register"):
                 log_to_go("ERROR", "Stuck at Register Button")
                 await browser.close(); return

            # --- 2. TERMS ---
            agree_txt = "Agree"
            if await page.get_by_text("Agree").count() == 0: agree_txt = "Next"
            
            if not await strict_navigator(page, agree_txt, ["Date of birth", "Use phone number"], "Terms"):
                 log_to_go("ERROR", "Stuck at Terms")
                 await browser.close(); return

            # --- 3. STATE MACHINE ---
            log_to_go("LOG", "🚦 Checking Flow...")
            
            # DOB Check
            if await page.get_by_text("Date of birth").count() > 0:
                log_to_go("LOG", "🔹 Detected DOB")
                await page.mouse.move(200, 500); await page.mouse.down()
                await page.mouse.move(200, 800, steps=10); await page.mouse.up()
                if not await strict_navigator(page, "Next", ["Use phone number"], "DOB"):
                    if not await page.get_by_text("Country/Region").count() > 0:
                        log_to_go("ERROR", "Stuck at DOB")
                        await browser.close(); return

            # Phone Option Check
            if await page.get_by_text("Use phone number").count() > 0:
                log_to_go("LOG", "🔹 Detected Phone Option")
                if not await strict_navigator(page, "Use phone number", ["Country/Region", "Hong Kong"], "Phone_Option"):
                     log_to_go("ERROR", "Stuck at Phone Option")
                     await browser.close(); return

            # --- 5. COUNTRY SWITCH ---
            log_to_go("LOG", f"🌍 Switching Country to {country}...")
            
            row_label = page.get_by_text("Country/Region").first
            if await row_label.count() > 0:
                box = await row_label.bounding_box()
                if box:
                    vp_width = 412; arrow_x = vp_width - 35; arrow_y = box['y'] + box['height'] / 2
                    
                    await single_capture(page, "05_Before_Arrow")

                    if await visual_tap(page, row_label, "Arrow"): # visual_tap now uses JS Click too
                        # Manual tap on arrow coords to be safe
                        await page.mouse.click(arrow_x, arrow_y)
                        
                        await step_wait(page, 2, "06_List_Opened")
                        
                        # --- SEARCH LOGIC ---
                        search_inp = page.locator("input").first
                        if await search_inp.count() > 0:
                            await visual_tap(page, search_inp, "Search Input")
                            await asyncio.sleep(0.5)
                            
                            log_to_go("LOG", f"⌨️ Typing: {country}")
                            await search_inp.fill("") 
                            await page.keyboard.type(country, delay=100)
                            await step_wait(page, 2, "07_Typed")
                            
                            # Result Selection (Below Input)
                            input_box = await search_inp.bounding_box()
                            result_y = input_box['y'] + input_box['height'] + 60 
                            
                            log_to_go("LOG", "👇 Clicking Result...")
                            # Force click coordinates
                            await page.mouse.click(200, result_y)
                            
                            await step_wait(page, 3, "08_Selected")
                        else:
                            log_to_go("ERROR", "Search Input Not Found")
                            await browser.close(); return
                    else:
                        log_to_go("ERROR", "Failed to tap Arrow")
                        await browser.close(); return
            else:
                log_to_go("ERROR", "Country Row Not Found")
                await browser.close(); return

            # --- 6. INPUT NUMBER ---
            log_to_go("LOG", f"⌨️ Inputting Number: {phone_number}")
            inp = page.locator("input[type='tel']").first
            if await inp.count() == 0: inp = page.locator("input").first
            
            if await inp.count() > 0:
                await visual_tap(page, inp, "Input Field")
                for c in phone_number:
                    await page.keyboard.type(c); await asyncio.sleep(0.05)
                await page.touchscreen.tap(350, 100) 
                
                await step_wait(page, 1, "09_Number_Entered")
                
                get_code = page.locator(".get-code-btn").first
                if await get_code.count() == 0: get_code = page.get_by_text("Get code").first
                
                if await get_code.count() > 0:
                    await visual_tap(page, get_code, "GET CODE")
                    log_to_go("LOG", "⏳ Checking Result...")
                    await single_capture(page, "10_GetCode_Clicked")
                    
                    start_time = time.time()
                    while True:
                        if time.time() - start_time > 60:
                            log_to_go("RETRY", "Timeout waiting for Captcha.")
                            await browser.close(); return

                        captcha_frame = None
                        for frame in page.frames:
                            try:
                                if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                    captcha_frame = frame; break
                            except: pass
                        
                        if captcha_frame:
                            log_to_go("LOG", "🧩 CAPTCHA FOUND!")
                            session_id = f"sess_{int(time.time())}"
                            await single_capture(page, "11_Captcha_Found")

                            ai_success = await solve_captcha(page, session_id, logger=log_to_go)
                            
                            if not ai_success:
                                log_to_go("RETRY", "AI Failed. Retrying...")
                                await browser.close(); return
                            
                            await step_wait(page, 5, "12_Post_Solve")
                            
                            is_still_there = False
                            for frame in page.frames:
                                try:
                                    if await frame.get_by_text("swap 2 tiles", exact=False).count() > 0:
                                        is_still_there = True; break
                                except: pass
                            
                            if not is_still_there:
                                log_to_go("SUCCESS", "✅ Verified! CAPTCHA GONE.")
                                await step_wait(page, 2, "13_Success_Proof")
                                await browser.close()
                                return
                            else:
                                await asyncio.sleep(2)
                                continue
                        else:
                            await asyncio.sleep(1)
                else:
                    log_to_go("ERROR", "Get Code Button not found!")
                    await browser.close(); return
            else:
                log_to_go("ERROR", "Input field not found!")
                await browser.close(); return

        except Exception as e:
            log_to_go("RETRY", f"Crash: {str(e)}")
            await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--number", required=True)
    parser.add_argument("--country", default="Russia")
    parser.add_argument("--proxy", default="")
    args = parser.parse_args()
    asyncio.run(run_session(args.number, args.country, args.proxy))