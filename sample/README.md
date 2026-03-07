# PGO Sample Videos

This directory is for **Profile-Guided Optimization (PGO)** sample videos.

## What is PGO?

Profile-Guided Optimization trains FFmpeg during compilation using real video samples. This produces an optimized binary that runs faster for your specific content type.

## How to Use

Place `.mkv` sample files in this directory **before building** the Docker image. Name files with the preset prefix so PGO training uses the matching encoder settings:

```
sample/
  default_movie.4k.mkv     # Live-action (CRF 26, film-grain=8)
  animated_show.1080p.mkv   # Animated (CRF 35, tune=0)
  grainy_film.1080p.mkv     # Grainy (CRF 26, film-grain=16, film-grain-denoise=1)
```

Files without a recognized prefix (`default_`, `animated_`, `grainy_`) are trained with the default preset.

## Sample Requirements

- **Format**: Matroska (`.mkv`)
- **Duration**: At least 10-15 seconds each
- **Content**: Representative of what you'll be converting
- **Codecs**: H.264 or HEVC (typical source formats)
- **Resolution**: Include a 4K sample to train the downscale code path (all presets default to 1080p cap)
- **HDR**: Include an HDR10 or HLG sample to train HDR color handling

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
