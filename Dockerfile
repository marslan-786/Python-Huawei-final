FROM python:3.11-bookworm

# 1. Install System Dependencies (Desktop & Chrome)
# Hum heavy dependencies daal rahe hain taake browser crash na ho
RUN apt-get update && apt-get install -y \
    chromium \
    ffmpeg \
    libnss3 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Install Python Packages
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

# 3. Install Playwright Browsers
RUN playwright install chromium
RUN playwright install-deps

# 4. Copy Code
COPY . .
RUN mkdir -p captures && chmod 777 captures

# 5. Start Command
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"