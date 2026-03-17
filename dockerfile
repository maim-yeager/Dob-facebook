# Official Python 3.10 image ব্যবহার করুন
FROM python:3.10-slim

# System dependencies আপডেট এবং Playwright এর জন্য必要な libraries install করুন
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Playwright কে system-এর Chromium ব্যবহার করতে বলুন
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin

# কাজের ডিরেক্টরি সেট করুন
WORKDIR /app

# প্রথমে requirements.txt কপি করুন (Docker cache ব্যবহারের জন্য)
COPY requirements.txt .

# Python dependencies install করুন এবং Playwright browsers install করুন
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

# সম্পূর্ণ প্রজেক্ট কপি করুন
COPY . .

# Bot চালানোর কমান্ড
CMD ["python", "maim_script.py"]
