import os
import zipfile
import shutil
import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from rembg import remove, new_session

app = FastAPI()
templates = Jinja2Templates(directory=".")

# --- GLOBAL STATUS ---
STATUS = {"state": "idle", "message": "Ready to upload"}
ZIP_FILENAME = "Final_Results.zip"
BASE_FOLDER = "processed_results"

# Load Models Once
session_u2net = new_session("u2net")
session_isnet = new_session("isnet-general-use")

# --- HEAVY PROCESSING FUNCTION ---
def run_heavy_processing(files_data):
    global STATUS
    STATUS = {"state": "processing", "message": f"Processing {len(files_data)} images..."}
    
    if os.path.exists(BASE_FOLDER): shutil.rmtree(BASE_FOLDER)
    os.makedirs(BASE_FOLDER)
    
    # Subfolders
    folders = {
        "1_Rembg_Standard": os.path.join(BASE_FOLDER, "1_Rembg_Standard"),
        "2_Rembg_Heavy": os.path.join(BASE_FOLDER, "2_Rembg_Heavy"),
        "3_CV_Threshold": os.path.join(BASE_FOLDER, "3_CV_Threshold"),
        "4_CV_Edges": os.path.join(BASE_FOLDER, "4_CV_Edges"),
        "5_CV_Adaptive": os.path.join(BASE_FOLDER, "5_CV_Adaptive"),
        "6_CV_Color_Iso": os.path.join(BASE_FOLDER, "6_CV_Color_Iso"),
    }
    for f in folders.values(): os.makedirs(f, exist_ok=True)

    total = len(files_data)
    processed = 0

    for filename, content in files_data:
        try:
            # 1. Rembg Standard
            with open(f"{folders['1_Rembg_Standard']}/{filename}.png", "wb") as f:
                f.write(remove(content, session=session_u2net))
            
            # 2. Rembg Heavy
            with open(f"{folders['2_Rembg_Heavy']}/{filename}.png", "wb") as f:
                f.write(remove(content, session=session_isnet))

            # OpenCV
            nparr = np.frombuffer(content, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
                # 3. Threshold
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                cv2.imwrite(f"{folders['3_CV_Threshold']}/{filename}", thresh)

                # 4. Edges
                blurred = cv2.GaussianBlur(gray, (5, 5), 0)
                edges = cv2.Canny(blurred, 50, 150)
                cv2.imwrite(f"{folders['4_CV_Edges']}/{filename}", edges)

                # 5. Adaptive
                adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
                cv2.imwrite(f"{folders['5_CV_Adaptive']}/{filename}", adapt)

                # 6. Color Iso
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 255, 100]))
                iso = cv2.bitwise_and(img, img, mask=mask)
                cv2.imwrite(f"{folders['6_CV_Color_Iso']}/{filename}", iso)

            processed += 1
            STATUS["message"] = f"Processed {processed}/{total} images..."
            
        except Exception as e:
            print(f"Error: {e}")

    # Zip It
    if os.path.exists(ZIP_FILENAME): os.remove(ZIP_FILENAME)
    with zipfile.ZipFile(ZIP_FILENAME, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(BASE_FOLDER):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, os.path.relpath(file_path, BASE_FOLDER))
    
    shutil.rmtree(BASE_FOLDER)
    STATUS = {"state": "done", "message": "Process Complete!"}

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/status")
async def get_status():
    return STATUS

@app.post("/upload")
async def start_process(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    global STATUS
    if STATUS["state"] == "processing":
        return {"error": "Already processing! Wait."}
    
    # Read files into memory to pass to background task
    files_data = []
    for file in files:
        content = await file.read()
        files_data.append((file.filename, content))
    
    # Start Background Task
    background_tasks.add_task(run_heavy_processing, files_data)
    return {"status": "started"}

@app.get("/download")
async def download_file():
    if os.path.exists(ZIP_FILENAME) and STATUS["state"] == "done":
        return FileResponse(ZIP_FILENAME, filename=ZIP_FILENAME, media_type='application/zip')
    return {"error": "File not ready yet"}