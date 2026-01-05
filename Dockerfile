# 1. Base Image
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 2. Work Directory
WORKDIR /app

# 3. Install System Dependencies (For OpenCV & AI)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 4. Requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

# 5. Install Browsers
RUN playwright install chromium

# 6. Copy Code
COPY . .

# 7. Create Captures Folder
RUN mkdir -p captures && chmod 777 captures

# 8. Start Command (Railway Specific Fix)
# Railway automatically injects the PORT variable. We must use it.
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"