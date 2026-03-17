FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY . .

# Render পোর্ট ব্যবহার করবে
CMD ["python", "maim_script.py"]
