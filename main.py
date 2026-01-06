import os
import io
import cv2
import numpy as np
import zipfile
import shutil
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from rembg import remove, new_session
from PIL import Image

app = FastAPI()
templates = Jinja2Templates(directory=".")

# --- 🔥 HEAVY SESSIONS (LOADED ONCE) 🔥 ---
# Standard Model
session_u2net = new_session("u2net")
# Heavy/General Use Model (Better accuracy)
session_isnet = new_session("isnet-general-use")

def process_image_cv_threshold(img_cv):
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh

def process_image_cv_edges(img_cv):
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    return edges

def process_image_cv_adaptive(img_cv):
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    return adaptive

def process_color_isolation(img_cv):
    # Convert to HSV to handle colors better
    hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
    # Define range for "common" background colors (like gray/white) to mask OUT
    # This acts as an inverse filter
    lower = np.array([0, 0, 0])
    upper = np.array([180, 255, 100]) # Filter dark areas
    mask = cv2.inRange(hsv, lower, upper)
    result = cv2.bitwise_and(img_cv, img_cv, mask=mask)
    return result

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def process_images(files: list[UploadFile] = File(...)):
    # Create temp directory
    base_folder = "processed_results"
    if os.path.exists(base_folder):
        shutil.rmtree(base_folder)
    os.makedirs(base_folder)

    # Subfolders for each engine
    folders = {
        "1_Rembg_Standard": os.path.join(base_folder, "1_Rembg_Standard"),
        "2_Rembg_Heavy_ISNet": os.path.join(base_folder, "2_Rembg_Heavy_ISNet"),
        "3_CV_Threshold": os.path.join(base_folder, "3_CV_Threshold"),
        "4_CV_Edges": os.path.join(base_folder, "4_CV_Edges"),
        "5_CV_Adaptive": os.path.join(base_folder, "5_CV_Adaptive"),
        "6_CV_Color_Iso": os.path.join(base_folder, "6_CV_Color_Iso"),
    }

    for f in folders.values():
        os.makedirs(f, exist_ok=True)

    print(f"🚀 Processing {len(files)} images...")

    for file in files:
        contents = await file.read()
        filename = file.filename
        
        # --- 1 & 2. AI REMOVERS (Rembg) ---
        try:
            # Standard
            output_u2 = remove(contents, session=session_u2net)
            with open(f"{folders['1_Rembg_Standard']}/{filename}.png", "wb") as f: f.write(output_u2)
            
            # Heavy (ISNet)
            output_is = remove(contents, session=session_isnet)
            with open(f"{folders['2_Rembg_Heavy_ISNet']}/{filename}.png", "wb") as f: f.write(output_is)
        except Exception as e:
            print(f"AI Error on {filename}: {e}")

        # --- OpenCV Processing ---
        # Convert bytes to OpenCV format
        nparr = np.frombuffer(contents, np.uint8)
        img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img_cv is not None:
            # 3. Threshold
            thresh = process_image_cv_threshold(img_cv)
            cv2.imwrite(f"{folders['3_CV_Threshold']}/{filename}", thresh)

            # 4. Edges
            edges = process_image_cv_edges(img_cv)
            cv2.imwrite(f"{folders['4_CV_Edges']}/{filename}", edges)

            # 5. Adaptive
            adapt = process_image_cv_adaptive(img_cv)
            cv2.imwrite(f"{folders['5_CV_Adaptive']}/{filename}", adapt)

            # 6. Color Iso
            col_iso = process_color_isolation(img_cv)
            cv2.imwrite(f"{folders['6_CV_Color_Iso']}/{filename}", col_iso)

    # --- ZIP EVERYTHING ---
    zip_filename = "All_Background_Removers_Result.zip"
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(base_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, base_folder)
                zipf.write(file_path, arcname)

    # Clean up
    shutil.rmtree(base_folder)

    return FileResponse(zip_filename, filename=zip_filename, media_type='application/zip')