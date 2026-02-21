# Video Conversion Service

[![CI](https://github.com/fabianwimberger/archive-video-av1/actions/workflows/ci.yml/badge.svg)](https://github.com/fabianwimberger/archive-video-av1/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A self-hosted, web-based video conversion service running in Docker. Converts video files to AV1 (via SVT-AV1) with real-time progress tracking, batch processing, and an intuitive browser UI.

## Why This Project?

AV1 offers superior compression efficiency compared to H.264 and H.265, reducing file sizes by 30-50% while maintaining quality. However, AV1 encoding is computationally intensive and most existing tools are either CLI-only or require complex setups. This project provides a simple, web-based interface for batch converting video libraries to AV1 without sacrificing quality.

## Features

- **Web UI** with real-time progress (FPS, ETA, percentage) via WebSocket
- **AV1 encoding** using SVT-AV1 with PGO-optimized FFmpeg
- **Batch processing** with sequential job queue
- **Conversion presets:**
  - **Standard** — CRF 26, film grain preservation (`film-grain=8`)
  - **Animated** — CRF 40, higher compression for animated content
  - **Grainy** — CRF 26, heavy grain preservation (`film-grain=16:film-grain-denoise=1`)
- **Automatic crop detection** (consensus-based, 8-point sampling)
- **Two-pass audio normalization** (loudnorm, Opus stereo output)
- **Language-aware track selection** (German > English > first available)
- **Skips re-encoding** if source is already AV1
- **Stateless** — database is wiped on restart; no persistent state to manage

## Quick Start

```bash
# Clone the repository
git clone https://github.com/fabianwimberger/archive-video-av1.git
cd archive-video-av1

# Configure volume mount in docker-compose.yml
# volumes:
#   - /path/to/your/videos:/videos

# Build and run
docker compose build
docker compose up -d

# Open UI at http://localhost:8000
```

## How It Works

1. **File browser** shows `.mkv` files from the mounted volume
2. **Select files** and choose conversion settings
3. **Jobs are queued** and processed sequentially in the background
4. **FFmpeg pipeline per file:**
   - Detect video codec (skip re-encode if AV1)
   - Crop detection via 8-point consensus sampling
   - Two-pass loudnorm audio measurement and normalization
   - SVT-AV1 encoding with progress output
   - `mkvmerge` finalization with metadata
5. **Real-time updates** are pushed to the browser via WebSocket
6. **Output** is saved alongside the source with `_conv` suffix

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOURCE_MOUNT` | `/videos` | Mount point for source video files |
| `TEMP_DIR` | `/app/temp` | Temporary directory for in-progress conversions |
| `DATABASE_PATH` | `/app/data/app.db` | SQLite database path (wiped on restart) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TZ` | `UTC` | Container timezone |

### Performance Optimizations

The Docker build compiles FFmpeg from source with:

- **PGO (Profile-Guided Optimization)** — train on your actual content for optimal codepath selection
- **LTO (Link-Time Optimization)** — whole-program analysis
- **`-march=native`** — optimized for the host CPU architecture
- **`-O3`** — maximum optimization level

> **Note:** Because of `-march=native`, the built image is tied to the CPU architecture it was built on. Rebuild when moving to different hardware.

## Project Structure

```
backend/
  app/
    main.py                  # FastAPI entry point
    config.py                # Environment-based settings
    services/                # Business logic
    routes/                  # API endpoints
    models/                  # Database models
frontend/
  index.html                 # Single-page app
  css/styles.css             # Layout & styles
  js/                        # JavaScript modules
scripts/
  build.sh                   # FFmpeg build with PGO
  conversion_wrapper.sh      # Per-file conversion pipeline
```

## Security

This application has **no built-in authentication**. It is intended to run on a trusted local network or behind a reverse proxy with authentication.

### Recommended: Bind to Local Interface

```yaml
ports:
  - "127.0.0.1:8000:8000"  # Only accessible locally
```

### Alternative: Reverse Proxy

See [docker-compose.override.yml](docker-compose.override.yml) example for Traefik configuration with basic auth.

## License

MIT License — see [LICENSE](LICENSE) file.

### FFmpeg Notice

This software uses [FFmpeg](https://ffmpeg.org/), which is licensed under the LGPL/GPL. When building and distributing Docker images containing FFmpeg, you must comply with the [FFmpeg license terms](https://ffmpeg.org/legal.html).
