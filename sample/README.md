# PGO Sample Videos

This directory is for **Profile-Guided Optimization (PGO)** sample videos.

## What is PGO?

Profile-Guided Optimization trains FFmpeg during compilation using real video samples. This produces an optimized binary that runs faster for your specific content type.

## How to Use

Place `.mkv` sample files in this directory **before building** the Docker image:

```
sample/
  normal_clip.mkv      # Live-action content (movies, TV shows)
  animated_clip.mkv    # Animated content (anime, cartoons)
```

## Sample Requirements

- **Format**: Matroska (`.mkv`)
- **Duration**: At least 10-15 seconds each
- **Content**: Representative of what you'll be converting
- **Codecs**: H.264 or HEVC (typical source formats)

## Benefits

- **5-15% faster encoding** compared to generic builds
- Optimized code paths for your specific video characteristics
- Better CPU instruction cache utilization

## Skip PGO

If you don't want PGO optimization, build with:

```bash
docker compose build --build-arg ENABLE_PGO=false
```

Or set in `docker-compose.yml`:

```yaml
services:
  convert-service:
    build:
      args:
        ENABLE_PGO: "false"
```

## Note

Files in this directory are **not** included in the final Docker image. They are only used during the build process.
