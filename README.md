# 🚀 GameOver YouTube High-Speed Streaming API

A private, lightning-fast YouTube resolution and streaming microservice designed specifically for Telegram Music & Video Bots (PyTgCalls / NTgCalls WebRTC).

Developed for deployment on **Linode Ubuntu VPS** (`172.104.38.31`) running on **Port 3000**.

---

## 🌟 Key Features & Advantages

1. **Ultra Fast Speed**:
   - Audio resolution and stream generation: **1–2 seconds**.
   - Video resolution and stream generation: **2–4 seconds**.
   - Repeated songs/videos (NVMe Cache Hit): **< 0.05 seconds**.
2. **True Video Streaming (No Screen Freeze)**:
   - Native H.264 MP4 stream matching the user's requested quality (`480p`, `360p`, `720p`).
   - Clean continuous video frames for Telegram Video Chat without static thumbnails or stuck screens.
3. **Optimized Stream URLs**:
   - Audio files: `/media/audio_{id}.mp3`
   - Video files: `/media/video_{id}.mp4`
4. **Range Requests (HTTP 206 Partial Content)**:
   - Built-in support for HTTP Range bytes, allowing PyTgCalls C++ core to buffer and seek seamlessly.
5. **Zero Cookie Expire & No BotGuard Blocks**:
   - Uses Android & iOS mobile client extractors, bypassing web bot detection on datacenter IPs.
6. **Automated NVMe Cache Cleaner**:
   - Runs in the background to delete cache files older than 24 hours, keeping disk storage healthy.

---

## 📡 API Endpoints

### 1. Unified Download Endpoint (`/download`)

> **Note on Parameter Ordering:**
> Parameters like `type` and `quality` come first, and the search query or video URL comes at the **end**:

#### 🎵 Audio Resolution (Default)
```bash
curl -s "http://172.104.38.31:3000/download?type=audio&url=tum+ho"
```
Or by YouTube ID:
```bash
curl -s "http://172.104.38.31:3000/download?type=audio&url=sK7riqg2mr4"
```

**JSON Response:**
```json
{
  "status": "success",
  "id": "sK7riqg2mr4",
  "title": "Tum Ho - Rockstar",
  "duration": "05:18",
  "duration_sec": 318,
  "thumbnail": "https://i.ytimg.com/vi/sK7riqg2mr4/hqdefault.jpg",
  "stream_url": "http://172.104.38.31:3000/media/audio_sK7riqg2mr4.mp3",
  "type": "audio",
  "quality": "192kbps",
  "cached": false,
  "elapsed_sec": 1.45
}
```

#### 🎬 Video Resolution (480p / 360p / 720p)
```bash
curl -s "http://172.104.38.31:3000/download?type=video&quality=480&url=5dYirJj0I9M"
```

**JSON Response:**
```json
{
  "status": "success",
  "id": "5dYirJj0I9M",
  "title": "Car Culture (Official Video) : PARMISH VERMA",
  "duration": "03:30",
  "duration_sec": 210,
  "thumbnail": "https://i.ytimg.com/vi/5dYirJj0I9M/hqdefault.jpg",
  "stream_url": "http://172.104.38.31:3000/media/video_5dYirJj0I9M.mp4",
  "type": "video",
  "quality": "480p",
  "cached": false,
  "elapsed_sec": 2.85
}
```

---

### 2. Media Streaming Endpoint (`/media/{filename}`)

Direct HTTP stream with range support for PyTgCalls:
- Audio: `http://172.104.38.31:3000/media/audio_sK7riqg2mr4.mp3`
- Video: `http://172.104.38.31:3000/media/video_5dYirJj0I9M.mp4`

---

### 3. System Health & Monitoring (`/health`)
```bash
curl -s "http://172.104.38.31:3000/health"
```
**JSON Response:**
```json
{
  "status": "healthy",
  "uptime_sec": 420.5,
  "cpu_percent": 2.4,
  "ram_used_percent": 18.2,
  "ram_available_mb": 3150.0,
  "cache": {
    "cached_files_count": 12,
    "cache_size_mb": 145.2,
    "disk_free_gb": 64.8
  },
  "port": 3000
}
```

---

## 🛠️ Deployment on Linode VPS (`172.104.38.31`)

### Method 1: WinSCP Drag & Drop (Fastest & Easiest)

1. Open **WinSCP** on your PC and connect to your VPS:
   - **Host:** `172.104.38.31`
   - **User:** `root`
   - **Port:** `22`
2. Drag the entire folder `GameOver YouTube API` into `/root/` on your VPS (name it `/root/gameover-api`).
3. Open SSH / Terminal and run:
   ```bash
   cd /root/gameover-api
   bash setup_vps.sh
   ```
4. Done! The installer automatically installs FFmpeg, creates the Python environment, registers the `systemd` 24/7 service, and starts it on Port 3000.

---

### Method 2: Manual Start (Testing)
If you just want to run it directly to test:
```bash
cd /root/gameover-api
bash start.sh
```

---

## 📋 Helpful Management Commands

Check service status:
```bash
sudo systemctl status gameover-api
```

Live real-time logs:
```bash
sudo journalctl -u gameover-api -f
```

Restart API service:
```bash
sudo systemctl restart gameover-api
```

Stop API service:
```bash
sudo systemctl stop gameover-api
```

---

## 🔒 Linode Firewall Reminder

Make sure your Linode Cloud Firewall `Hostingbot (ID: 64187026)` has an inbound rule allowing **TCP Port 3000** from all IPs (`0.0.0.0/0`).
Ports 8000 and 5000 remain untouched and running.
