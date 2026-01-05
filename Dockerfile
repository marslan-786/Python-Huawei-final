# 1. Official Playwright Image (Heavy & Ready)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 2. Setup Work Directory
WORKDIR /app

# 3. 🔥 Install System Dependencies for OpenCV & AI (Zaroori hai)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 4. Copy Requirements
COPY requirements.txt .

# 5. Install Python Packages (Heavy Installation)
RUN pip install --no-cache-dir -U -r requirements.txt

# 6. Install Browsers (Chromium Only)
RUN playwright install chromium

# 7. Copy Your Code
COPY . .

# 8. Create Captures Folder & Permissions
RUN mkdir -p captures && chmod 777 captures

# 9. Start Command
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"