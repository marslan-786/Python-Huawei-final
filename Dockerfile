FROM python:3.10-slim

# Install system dependencies for OpenCV
# FIX: 'libgl1-mesa-glx' is replaced by 'libgl1' in newer Debian versions
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Expose port (Railway sets $PORT automatically)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}