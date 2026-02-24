# Video Conversion Service

[![CI](https://github.com/fabianwimberger/archive-video-av1/actions/workflows/ci.yml/badge.svg)](https://github.com/fabianwimberger/archive-video-av1/actions)
[![Docker](https://github.com/fabianwimberger/archive-video-av1/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/fabianwimberger/archive-video-av1/pkgs/container/archive-video-av1)
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

### Option 1: Using Pre-built Image (Recommended)

Pre-built images support both **AMD64** and **ARM64** architectures.

**Docker Compose:**

```bash
# Clone the repository for docker-compose.yml
git clone https://github.com/fabianwimberger/archive-video-av1.git
cd archive-video-av1

# Configure volume mount in docker-compose.yml
# volumes:
#   - /path/to/your/videos:/videos

# Run with pre-built image
docker compose up -d

# Open UI at http://localhost:8000
```

**Or with docker run:**

```bash
docker run -d \
  --name convert-service \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /path/to/your/videos:/videos \
  -e TZ=UTC \
  -e SOURCE_MOUNT=/videos \
  -e LOG_LEVEL=INFO \
  ghcr.io/fabianwimberger/archive-video-av1:latest
```

### Option 2: Build from Source (with PGO optimization)

```bash
# Clone the repository
git clone https://github.com/fabianwimberger/archive-video-av1.git
cd archive-video-av1

# Copy the override file to configure your video path
cp docker-compose.override.yml.example docker-compose.override.yml

# Edit the override file to set your video path:
# volumes:
#   - /path/to/your/videos:/videos

# Build and run (PGO enabled by default for maximum performance)
make build
make up

# Or using docker compose directly:
# docker compose build --build-arg ENABLE_PGO=true
# docker compose up -d

# Open UI at http://localhost:8000
```

**Build Differences:**

| Build Type | PGO | Architecture | Best For |
|------------|-----|--------------|----------|
| Pre-built images | ❌ Disabled | Generic (no `-march`) | Portable, multi-arch (amd64/arm64) |
| Local `make build` | ✅ Enabled | Native (`-march=native`) | Maximum performance on your CPU |

To disable PGO for local builds: `ENABLE_PGO=false make build`

### Available Image Tags

The following image tags are available from `ghcr.io/fabianwimberger/archive-video-av1`:

| Tag | Description |
|-----|-------------|
| `main` | Latest development build from main branch |
| `v1.2.3` | Specific release version |
| `v1.2` | Latest patch release in the v1.2.x series |
| `v1` | Latest minor release in the v1.x.x series |
| `<short-sha>` | Specific commit SHA (e.g., `abc1234`)

### Updating

```bash
# Pull latest image
docker compose pull
docker compose up -d

# Or with docker run
docker pull ghcr.io/fabianwimberger/archive-video-av1:latest
docker restart convert-service
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

- **PGO (Profile-Guided Optimization)** — train on your actual content for optimal codepath selection (local builds only)
- **LTO (Link-Time Optimization)** — whole-program analysis
- **Architecture-specific optimizations** — native for local builds, generic for pre-built images
- **`-O3`** — maximum optimization level

**Pre-built Images (GitHub Registry):**
- Multi-arch support: `linux/amd64` and `linux/arm64`
- Generic architecture flags for portability
- PGO disabled for reproducible builds

**Local Builds (`make build`):**
- Uses `-march=native` for your specific CPU
- PGO enabled by default for maximum performance
- Run `ENABLE_PGO=false make build` to disable PGO
- **Note:** Because of `-march=native`, locally built images are tied to the CPU architecture they were built on. Rebuild when moving to different hardware.

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

See [docker-compose.yml](docker-compose.yml) example for Traefik configuration with basic auth.

## License

MIT License — see [LICENSE](LICENSE) file.

### Third-Party Licenses

This software includes the following open-source components:

| Component | License | Source |
|-----------|---------|--------|
| FFmpeg | [GPL v2+](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html) | https://git.ffmpeg.org/ffmpeg.git |
| SVT-AV1 | [BSD-3-Clause](https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/LICENSE.md) | https://gitlab.com/AOMediaCodec/SVT-AV1 |
| Opus | [BSD-3-Clause](https://opus-codec.org/license/) | https://opus-codec.org/ |

When using the pre-built Docker image, FFmpeg is compiled with GPL enabled. The FFmpeg license notice is included in the image at `/usr/share/licenses/FFmpeg-LICENSE`.
