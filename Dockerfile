FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    PORT=7860 \
    API_PORT=7860 \
    API_HOST=0.0.0.0 \
    DATA_DIR=/data

# Install system dependencies (FFmpeg, curl, git, build tools, SSL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    libffi-dev \
    libssl-dev \
    ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements & install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port 7860 for Hugging Face Spaces
EXPOSE 7860

# Run GameOver YouTube API
CMD ["python3", "-u", "main.py"]
