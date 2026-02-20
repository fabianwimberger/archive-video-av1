# Video Conversion Service

[![CI](https://github.com/fabianwimberger/convert-video-docker/actions/workflows/ci.yml/badge.svg)](https://github.com/fabianwimberger/convert-video-docker/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A self-hosted, web-based video conversion service running in Docker. Converts video files to AV1 (via SVT-AV1) with real-time progress tracking, batch processing, and an intuitive browser UI.

> **Disclaimer:** This software is provided as-is without any warranties. Use at your own risk. The authors are not responsible for any data loss or damages that may occur.

> **Security:** This service has **no built-in authentication**. Do not expose it to the internet without a reverse proxy and proper auth. See [Security](#security--reverse-proxy).

## Features

- **Web UI** with real-time progress (FPS, ETA, percentage) via WebSocket
- **AV1 encoding** using SVT-AV1 with PGO-optimized FFmpeg
- **Batch processing** with sequential job queue
- **Conversion presets:**
  - **Standard** -- CRF 26, film grain preservation (`film-grain=8`)
  - **Animated** -- CRF 40, higher compression for animated content
  - **Grainy** -- CRF 26, heavy grain preservation (`film-grain=16:film-grain-denoise=1`)
- **Automatic crop detection** (consensus-based, 8-point sampling)
- **Two-pass audio normalization** (loudnorm, Opus stereo output)
- **Language-aware track selection** (German > English > first available)
- **Skips re-encoding** if source is already AV1
- **Stateless** -- database is wiped on restart; no persistent state to manage

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/fabianwimberger/convert-video-docker.git
cd convert-video-docker
```

Edit `docker-compose.yml` and set your video directory:

```yaml
volumes:
  - /path/to/your/videos:/videos
```

### 2. (Optional) Add PGO samples

For best encoding performance, place sample `.mkv` files in `sample/` before building. FFmpeg will be compiled with Profile-Guided Optimization trained on these samples.

```
sample/
  normal_clip.mkv      # Live-action sample
  animated_clip.mkv    # Anime/cartoon sample
```

To skip PGO, build with `--build-arg ENABLE_PGO=false`.

### 3. Build and run

```bash
docker compose build
docker compose up -d
```

### 4. Open the UI

Navigate to [http://localhost:8000](http://localhost:8000). Select files, choose a preset, and start converting.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOURCE_MOUNT` | `/videos` | Mount point for source video files |
| `TEMP_DIR` | `/app/temp` | Temporary directory for in-progress conversions |
| `DATABASE_PATH` | `/app/data/app.db` | SQLite database path (wiped on restart) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TZ` | `UTC` | Container timezone |

### Conversion Settings

| Parameter | Range | Default (Standard) | Description |
|-----------|-------|---------------------|-------------|
| CRF | 0-51 | 26 | Quality (lower = better quality, larger file) |
| Preset | 0-13 | 4 | Speed (lower = slower, better compression) |
| SVT Params | string | `tune=0:film-grain=8` | SVT-AV1 encoder parameters |
| Audio Bitrate | string | `96k` | Opus audio bitrate |
| Skip Crop | bool | false | Disable automatic black bar removal |

### Advanced: Override File

Use `docker-compose.override.yml` for local customizations:

```yaml
services:
  convert-service:
    environment:
      - TZ=America/New_York
    deploy:
      resources:
        limits:
          memory: 8G
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
6. **Output** is saved alongside the source with `_conv` suffix (`movie.mkv` -> `movie_conv.mkv`)

## Performance Optimizations

The Docker build compiles FFmpeg from source with:

- **PGO (Profile-Guided Optimization)** -- train on your actual content for optimal codepath selection
- **LTO (Link-Time Optimization)** -- whole-program analysis
- **`-march=native`** -- optimized for the host CPU architecture
- **`-O3`** -- maximum optimization level

> **Note:** Because of `-march=native`, the built image is tied to the CPU architecture it was built on. Rebuild when moving to different hardware.

## Security & Reverse Proxy

This application has **no authentication or encryption**. It is intended to run on a trusted local network or behind a reverse proxy.

### Recommended: Reverse Proxy with Auth

Create a `docker-compose.override.yml`:

```yaml
services:
  convert-service:
    ports: []  # Remove direct port exposure
    labels:
      - traefik.enable=true
      - traefik.http.routers.convert.rule=Host(`convert.yourdomain.com`)
      - traefik.http.routers.convert.entrypoints=websecure
      - traefik.http.routers.convert.tls.certresolver=letsencrypt
      - traefik.http.services.convert.loadbalancer.server.port=8000
      - traefik.http.routers.convert.middlewares=convert-auth
      - traefik.http.middlewares.convert-auth.basicauth.users=admin:$$apr1$$...
    networks:
      - traefik-network

networks:
  traefik-network:
    external: true
```

### Alternative: Bind to Local Interface

```yaml
ports:
  - "192.168.1.100:8000:8000"  # Only accessible from LAN
```

### Alternative: SSH Tunnel

```bash
ssh -L 8000:localhost:8000 user@your-server
# Then open http://localhost:8000
```

## API

Interactive docs are available at `/docs` (Swagger) and `/redoc` when the service is running.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check with queue status |
| `GET` | `/api/presets` | Get conversion presets |
| `GET` | `/api/files?path=...` | Browse directory |
| `GET` | `/api/files/info?path=...` | Get video file metadata |
| `POST` | `/api/jobs` | Create single conversion job |
| `POST` | `/api/jobs/batch` | Create batch conversion jobs |
| `GET` | `/api/jobs` | List jobs (filterable by status) |
| `GET` | `/api/jobs/{id}` | Get job details |
| `DELETE` | `/api/jobs/{id}` | Cancel or delete a job |
| `DELETE` | `/api/jobs/completed` | Clear finished jobs |
| `DELETE` | `/api/jobs/all` | Force clear all jobs |
| `WS` | `/ws` | WebSocket for real-time updates |

## Project Structure

```
backend/
  app/
    main.py                  # FastAPI entry point, lifespan, static files
    config.py                # Environment-based settings
    database.py              # SQLAlchemy async engine + session
    models/
      job.py                 # Job ORM model
      schemas.py             # Pydantic request/response schemas
    routes/
      files.py               # File browsing & deletion endpoints
      jobs.py                # Job CRUD & queue management
      websocket.py           # WebSocket endpoint
    services/
      conversion_service.py  # FFmpeg process execution & progress parsing
      file_service.py        # File system operations
      job_queue.py           # Async job queue worker
      websocket_manager.py   # WebSocket connection broadcasting
    utils/
      ffprobe.py             # Video metadata extraction
frontend/
  index.html                 # Single-page app shell
  css/styles.css             # Layout & custom styles
  js/
    app.js                   # Application bootstrap
    api.js                   # REST API client
    websocket-client.js      # WebSocket client with reconnection
    file-browser.js          # File browser component
    job-queue.js             # Job queue component
    settings-panel.js        # Conversion settings component
scripts/
  build.sh                   # FFmpeg/Opus/SVT-AV1 build with PGO
  conversion_wrapper.sh      # Per-file conversion pipeline
  download_vendors.py        # Download Bootstrap at build time
  fix_metadata.py            # Standalone: fix MKV metadata inconsistencies
```

## Troubleshooting

**Conversion fails immediately:**
```bash
docker logs convert-service
```
Check that the volume mount is correct and the container has write access.

**WebSocket shows "Disconnected":**
The UI falls back to polling automatically. Check that no firewall or proxy is blocking WebSocket connections on port 8000.

**Slow encoding:**
Lower the preset value (e.g., 4-6). Ensure the host CPU is not throttled. Check with `docker stats convert-service`.

**Out of memory:**
Add memory limits via `docker-compose.override.yml` and ensure sufficient RAM for your resolution (4K needs 4-8GB).

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Push and open a Pull Request

## License

[MIT](LICENSE)

## Acknowledgments

- [FFmpeg](https://ffmpeg.org/) -- Multimedia processing
- [SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1) -- AV1 encoder
- [FastAPI](https://fastapi.tiangolo.com/) -- Async Python web framework
- [Bootstrap](https://getbootstrap.com/) -- UI framework
