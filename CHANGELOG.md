# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.8.2] - 2026-09-23

Robustness fixes for conversions and cluster deployments, stricter PGO build verification, and a dependency refresh with FFmpeg 9.0.2.

### Fixes

- Outputs are published only after successful finalization; existing outputs are never overwritten, and files sharing a stem cannot be queued together
- Deleting an original requires a matching successful conversion record whose file sizes still match, with deletion guarded against symlinks and locked outputs
- Distributed queue preserves worker ownership across outages and deduplicates dispatch retries
- Cancelling or retrying a running job requires known worker state; node-local job IDs are distinguished in the queue and history
- Preset imports handle repeated names within one document safely
- PGO Docker builds fail instead of silently building without matching profile data

### Dependencies

- ffmpeg 9.0.1 → 9.0.2
- uvicorn 0.52.4 → 0.53.0
- sqlalchemy 2.0.52 → 2.0.54

### Documentation & Links

- [Full changelog](https://github.com/fabianwimberger/archive-video-av1/compare/v1.8.1...v1.8.2)

## [v1.8.1] - 2026-09-19

Fixes the history page being unscrollable with many entries, hardens vendor downloads, and corrects the SVT-AV1 licensing notes.

### Fixes

- History page now scrolls when the list is long, so older entries stay reachable instead of being clipped
- Verify the SHA-256 hash of every downloaded vendor asset and pin the expected version, so a tampered or unexpected file fails the build instead of shipping

### Documentation

- Correct the SVT-AV1 license and PGO claims in `DOCKER_LICENSES.md` and the README

### Dependencies

- Bump alembic from 1.19.0 to 1.20.0
- Bump websockets from 17.0.1 to 17.1

### Documentation & Links

- [Full changelog](https://github.com/fabianwimberger/archive-video-av1/compare/v1.8.0...v1.8.1)

## [v1.8.0] - 2026-08-15

Directory sort ordering, a Docker build hang, and a batch of dependency updates including FFmpeg 9.0.1.

### Fixes

- Directory and file listings now sort naturally, so "Season 2" comes before "Season 10" instead of after it
- Docker build no longer hangs on apt-get prompts (`DEBIAN_FRONTEND=noninteractive`)

### Dependencies

- ffmpeg 8.1.2 → 9.0.1
- fastapi 0.140.7 → 0.141.1
- uvicorn 0.51.0 → 0.52.1
- websockets 16.1.1 → 17.0.1
- alembic 1.18.5 → 1.19.0

### Documentation & Links

- [Full changelog](https://github.com/fabianwimberger/archive-video-av1/compare/v1.7.0...v1.8.0)

## [v1.7.0] - 2026-07-31

Concurrent video/audio encoding, a reworked SVT-AV1 tuning surface, and a collapsible settings UI.

### Features

- Video and audio now encode as concurrent branches instead of one serial pass, muxed back together via mkvmerge
- SVT-AV1 tuning overhaul: quantization matrices (luma + chroma), luminance-based QP bias for non-PQ sources, tri-state toggles for tune/film-grain/denoise/variance-boost/restoration instead of raw text entry, tf-strength/sharpness as real scalars
- Animated preset no longer inherits variance-boost/tf-strength from the general preset, since neither earns its bitrate cost on flat-shaded content
- Settings panel sections are now collapsible, and no longer clip on smaller viewports
- Conversion log reports an audio encode completion line (track count, bitrate, duration, output size)
- PGO training now mirrors the exact runtime preset parameters and the concurrent pipeline shape

### Fixes

- Duplicate chapters, a signal-handling edge case on cancellation/failure, fail-fast on branch failure, and audio branch niced to match video priority — all surfaced by the video/audio branch split
- Audio/subtitle track selection env vars weren't passed through correctly
- SVT toggles could silently force a flag off instead of leaving it at its encoder default (tri-state)
- FFmpeg build no longer passes a nonexistent `--disable-lto` flag
- Denoise toggle's indeterminate state styling and a dead grain_estimator branch

### Documentation & Links

- [Full changelog](https://github.com/fabianwimberger/archive-video-av1/compare/v1.6.0...v1.7.0)

## [v1.6.0] - 2026-07-17

This release picks up a newer AV1 encoder and routine backend dependency updates.

### Dependencies

- SVT-AV1 4.1.0 → 4.2.0
- fastapi 0.138.1 → 0.139.0
- uvicorn 0.49.0 → 0.51.0
- websockets 16.0 → 16.1

### Documentation & Links

- [SVT-AV1 v4.2.0](https://gitlab.com/AOMediaCodec/SVT-AV1/-/releases/v4.2.0)

## [v1.5.0] - 2026-06-28

This release upgrades the build toolchain and fixes a subtitle encoding failure.

### Fixes

- MP4 files with `mov_text` subtitles no longer fail to encode: the subtitle stream is now transcoded to SRT before muxing into MKV, which does not support the `mov_text` codec

### Features

- Base image upgraded from Ubuntu 26.04 to 26.10, picking up GCC 16.1.0 stable (26.04 shipped a pre-release snapshot from March 2026)
- PGO instrumentation phase now uses `-fprofile-update=prefer-atomic` instead of `atomic`
- PGO use phase now uses `-fprofile-partial-training` instead of `-fprofile-correction`: leaves untrained code paths unoptimized rather than applying heuristic corrections

### Dependencies

- fastapi 0.138.0 → 0.138.1
- alembic 1.18.4 → 1.18.5
- pytest 9.1.0 → 9.1.1

### Documentation & Links

- [GCC 16 release notes](https://gcc.gnu.org/gcc-16/changes.html)
- [SVT-AV1 v4.1.0](https://gitlab.com/AOMediaCodec/SVT-AV1/-/releases/v4.1.0)

## [v1.4.0] - 2026-06-24

This release fixes real multicast peer discovery for distributed cluster nodes, removing the need to hardcode peer IPs.

### Features

- Add support for `.mp4` as a source container.

### Fixes

- Fix cluster multicast discovery silently failing under uvloop by sending/receiving on the raw socket instead of unimplemented event-loop primitives.
- Receive cluster heartbeats via a non-blocking `add_reader` listener instead of a blocking thread, preventing shutdown hangs.
- Auto-detect each node's public URL via outbound route lookup instead of requiring a hardcoded `DISTRIBUTED_PUBLIC_URL`.

### Dependencies

- Bump ffmpeg to 8.1.2.
- Bump backend `fastapi` to 0.138.0.
- Bump backend `sqlalchemy` to 2.0.51.
- Bump backend `uvicorn` to 0.49.0.
- Bump backend `pytest` to 9.1.0.
- Bump backend `python-multipart` to 0.0.32.
- Bump backend `pytest-asyncio` to 1.4.0.
- Bump GitHub Actions `actions/checkout` to 7 and `codecov/codecov-action` to 7.

### Documentation & Links

- [README](https://github.com/fabianwimberger/archive-video-av1#readme)

## [v1.3.0] - 2026-05-31

Archive Video AV1 now supports opt-in distributed processing across trusted LAN nodes, so multiple machines can share AV1 queue work while keeping a cluster-wide queue view.

### Features

- Add distributed worker mode with peer discovery, leader election, remote job delegation, and cluster status reporting.
- Add queue replication so pending and active distributed jobs stay visible across nodes and can continue after leader changes.
- Show cluster status and worker assignments in the Active Queue UI.
- Add node-oriented Docker Compose and Makefile commands for distributed deployments.

### Fixes

- Keep distributed worker code passing Ruff format checks and mypy.
- Preserve the latest backend dependency updates when merging distributed worker support into `main`.

### Dependencies

- Updated backend `fastapi` to 0.136.3.
- Updated backend `uvicorn` to 0.48.0.
- Updated backend `sqlalchemy` to 2.0.50.

### Documentation & Links

- Documented distributed processing requirements, environment variables, and node startup commands.
- [README](https://github.com/fabianwimberger/archive-video-av1#readme)

## [v1.2.0] - 2026-05-22

Archive Video AV1 now has a responsive web UI that works more cleanly across desktop and phone-sized screens.

### Features

- Improve the convert and history views with responsive spacing, wrapping controls, compact queue items, and mobile-friendly tables.
- Add desktop and mobile screenshots to show the current browser UI.

### Fixes

- Keep queue and history actions usable on narrow screens.
- Improve modal sizing for phone-sized viewports.

### Dependencies

- Updated backend `python-multipart` to 0.0.29.
- Updated backend `uvicorn` to 0.47.0.

### Documentation & Links

- Documented the main Web UI workflows.
- [README](https://github.com/fabianwimberger/archive-video-av1#readme)

## [v1.1.0] - 2026-05-09

Archive Video AV1 now lets you choose how audio and subtitle streams are selected while keeping the German-first, English-fallback behavior as the default.

### Features

- Add `AUDIO_TRACK_MODE` for selecting one preferred audio track or copying all audio tracks
- Add `SUBTITLE_TRACK_MODE` for selecting one preferred subtitle track, copying all subtitle tracks, or dropping subtitles
- Add configurable preferred audio and subtitle language lists in Docker Compose

### Fixes

- Refuse source deletion unless the converted file exists and is non-empty
- Keep subtitle fallback working when no preferred audio language is found
- Improve file browsing performance by loading latest job data in one query
- Keep queue wake handling behind the queue service API
- Align backend API version metadata with this release

### Documentation & Links

- [README](https://github.com/fabianwimberger/archive-video-av1#readme)

## [v1.0.2] - 2026-05-08

Bug fixes for non-ASCII filename handling and the processing queue display, plus a major ffmpeg upgrade and routine dependency updates.

### Fixes

- Set ``C.UTF-8`` locale so mkvmerge handles non-ASCII filenames
- Pass ``C.UTF-8`` locale to conversion subprocess env
- Processing job no longer hidden when 100+ pending jobs in queue

### Dependencies

- Upgrade ffmpeg to 8.1.1
- Bump fastapi from 0.136.0 to 0.136.1 in /backend
- Bump uvicorn from 0.44.0 to 0.46.0 in /backend
- Bump python-multipart from 0.0.26 to 0.0.27 in /backend
- Bump pytest-cov from 7.0.0 to 7.1.0 in /backend

### CI

- Test with gcc-16

### Documentation & Links

- [README](https://github.com/fabianwimberger/archive-video-av1#readme)
- [Container image](https://github.com/fabianwimberger/archive-video-av1/pkgs/container/archive-video-av1)

## [v1.0.1] - 2026-04-25

Validation error handling fix, CI test coverage now reported to Codecov, and routine dependency updates.

### Fixes

- Return HTTP 422 for validation errors instead of 500

### CI

- Run pytest with coverage and upload to Codecov; coverage badge added to README

### Documentation

- Replaced why-this-project with a Background section

### Dependencies

- Bump fastapi from 0.135.3 to 0.136.0
- Bump alembic from 1.15.2 to 1.18.4
- Bump pytest from 8.3.5 to 9.0.3 in /backend
- Bump pytest-asyncio from 0.26.0 to 1.3.0 in /backend

### Chores

- Added community health files


### Documentation & Links

- [README](https://github.com/fabianwimberger/archive-video-av1#readme)
- [Container image](https://github.com/fabianwimberger/archive-video-av1/pkgs/container/archive-video-av1)

## [v1.0.0] - 2026-04-18

# archive-video-av1 v1.0.0

**The first official release of a self-hosted, web-based AV1 video conversion service.**

---

### Built-in Presets

| Preset | CRF | SVT-AV1 params |
|--------|-----|----------------|
| Default | 26 | tune=0, film-grain=8 |
| Animated | 35 | tune=0 |
| Grainy | 26 | tune=0, film-grain=16, film-grain-denoise=1 |

---

### Features

- **AV1 encoding** via SVT-AV1 with PGO-optimized FFmpeg
- **Preset management** — create, edit, duplicate, import/export custom presets
- **Persistent queue** — pause, resume, reorder; survives restarts
- **Job history** — filterable, searchable, with per-job detail and retry
- **Grain estimator** — auto-suggests film grain settings from frame analysis
- **Automatic crop detection** — consensus-based, 8-point sampling
- **Two-pass audio normalization** — loudnorm, Opus stereo output
- **Re-encode guard** — skips files already in AV1

---

### Quick Start

```bash
docker run -d \
  --name archive-video-av1 \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /path/to/your/videos:/videos \
  -v archive-video-av1-data:/app/data \
  -e SOURCE_MOUNT=/videos \
  ghcr.io/fabianwimberger/archive-video-av1:latest
```

Open the UI at **http://localhost:8000**

---

### Documentation & Links

- Docs: https://github.com/fabianwimberger/archive-video-av1#readme
- Docker image: https://github.com/fabianwimberger/archive-video-av1/pkgs/container/archive-video-av1
