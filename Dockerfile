# 1. Base Image (Playwright Official)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 2. Work Directory
WORKDIR /app

# 3. 🔥 Install System Dependencies (Ye line Railway par crash rokne ke liye zaroori hai)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 4. Copy Requirements
COPY requirements.txt .

# 5. Install Python Packages
RUN pip install --no-cache-dir -U -r requirements.txt

# 6. Install Browsers
RUN playwright install chromium

# 7. Copy Code
COPY . .

# 8. Create Captures Folder & Permissions
RUN mkdir -p captures && chmod 777 captures

# 9. Start Command (Railway Port Handling)
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"