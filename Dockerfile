# syntax=docker/dockerfile:1
FROM ubuntu:26.10 AS codec-source

ARG FFMPEG_VERSION="9.0.2"
ARG OPUS_VERSION="1.6.1"
ARG SVT_AV1_VERSION="4.3.0"
ARG ENABLE_PGO="false"
ARG ENABLE_LTO="true"
ARG ARCH_FLAGS

# Pinned checksums for the source tarballs below; update alongside the
# matching *_VERSION when bumping.
ARG FFMPEG_SHA256="84960df915059e8754fef2cd7c9afeb614062b1b5458ec471eecee619ee04e98"
ARG OPUS_SHA256="6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1"
ARG SVT_AV1_SHA256="95ce8fc2381ba707e65ac8b9b2e165915bfbe9cd4d2f7b0f58d64ce31bd98f6d"

ENV PGO_DIR="/build/profiles"
ENV ARCH_FLAGS=${ARCH_FLAGS}
ENV ENABLE_LTO=${ENABLE_LTO}
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    build-essential gcc-16 g++-16 cmake nasm pkg-config \
    wget ca-certificates tar xz-utils \
    autoconf automake libtool zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CC=gcc-16 CXX=g++-16

WORKDIR /build

# Keep the verified archives so each image can supply the source it used.
RUN mkdir /build/source && cd /build/source && \
    wget -nv "https://ftp.osuosl.org/pub/xiph/releases/opus/opus-${OPUS_VERSION}.tar.gz" && \
    echo "${OPUS_SHA256}  opus-${OPUS_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf "opus-${OPUS_VERSION}.tar.gz" -C /build && \
    wget -nv "https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v${SVT_AV1_VERSION}/SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" && \
    echo "${SVT_AV1_SHA256}  SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf "SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" -C /build && \
    wget -nv "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.gz" && \
    echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf "ffmpeg-${FFMPEG_VERSION}.tar.gz" -C /build && \
    mv "/build/ffmpeg-${FFMPEG_VERSION}" /build/FFmpeg && \
    install -Dm644 /build/FFmpeg/COPYING.GPLv2 /build/licenses/FFmpeg-LICENSE && \
    install -Dm644 "/build/SVT-AV1-v${SVT_AV1_VERSION}/LICENSE.md" /build/licenses/SVT-AV1-LICENSE && \
    install -Dm644 "/build/SVT-AV1-v${SVT_AV1_VERSION}/PATENTS.md" /build/licenses/SVT-AV1-PATENTS && \
    install -Dm644 "/build/opus-${OPUS_VERSION}/COPYING" /build/licenses/Opus-LICENSE

FROM codec-source AS builder

# Copy build script (cached unless script changes)
COPY scripts/build.sh /build/build.sh
RUN chmod +x /build/build.sh

# ARCH_FLAGS: empty for generic CI builds, -march=native from the Makefile.

# Build with PGO
# Keep instrumented compilation cached when only training samples change.
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-generate; \
    fi

# Copy samples after Layer 1, so a sample change doesn't invalidate it too.
COPY sample/ /build/samples/

# Layer 2: Run PGO training (rebuilds if samples change)
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-train; \
    fi

# Reuse build paths so SVT-AV1 and FFmpeg can locate their collected profiles.
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-use; \
    else \
        /build/build.sh standard; \
    fi

# Verification and stripping (always runs after successful build)
RUN echo "=== Checking profile availability ==="; \
    \
    if [ "$ENABLE_PGO" = "true" ]; then \
        profile_count=$(find "$PGO_DIR" -name '*.gcda' 2>/dev/null | wc -l); \
        if [ "$profile_count" -eq 0 ]; then \
            echo "WARNING: PGO was enabled but no profile data was generated (no sample videos found); built without PGO optimization"; \
        elif [ "$profile_count" -lt 10 ]; then \
            echo "ERROR: PGO was enabled but only $profile_count profile files were generated (expected at least 10)"; \
            echo "This indicates PGO training failed or samples were insufficient"; \
            exit 1; \
        else \
            echo "PGO profile files available: $profile_count (not a measure of profile coverage)"; \
        fi; \
    fi; \
    \
    echo "=== Stripping binaries ==="; \
    strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe || { echo "ERROR: Failed to strip binaries"; exit 1; }

FROM ubuntu:26.10 AS runtime-packages
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/venv/bin:$PATH" \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Fetch the Ubuntu packaging and patches for the GPL tools installed here.
RUN set -eu; \
    sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -qq; \
    apt-get upgrade -y -qq; \
    apt-get install -y -qq --no-install-recommends python3 python3-venv ca-certificates mkvtoolnix bash util-linux; \
    mkdir -p /usr/share/source/archive-video-av1/ubuntu; \
    cd /usr/share/source/archive-video-av1/ubuntu; \
    for package in bash mkvtoolnix util-linux; do \
        source_package="$(dpkg-query -W -f='${source:Package}=${source:Version}' "$package")"; \
        apt-get source -qq --download-only "$source_package"; \
    done; \
    dpkg-query -W -f='${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n' > ../ubuntu-packages.tsv; \
    sed -i 's/^Types: deb deb-src$/Types: deb/' /etc/apt/sources.list.d/ubuntu.sources; \
    rm -rf /var/lib/apt/lists/*

FROM runtime-packages
COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/
COPY --from=codec-source /build/licenses/ /usr/share/licenses/
COPY --from=codec-source /build/source/ /usr/share/source/archive-video-av1/
COPY Dockerfile /usr/share/source/archive-video-av1/
COPY scripts/build.sh /usr/share/source/archive-video-av1/scripts/
COPY LICENSE DOCKER_LICENSES.md /usr/share/licenses/archive-video-av1/

# The package is installed from the repository layout, then the app is
# placed where it runs from: it resolves alembic.ini and the frontend
# relative to its own location.
# uv is mounted for this step only, so it doesn't end up in the image.
COPY pyproject.toml uv.lock ./
COPY backend/app/ /app/backend/app/
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.7,source=/uv,target=/bin/uv \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3 UV_PROJECT_ENVIRONMENT=/app/venv \
    uv sync --frozen --no-editable --no-cache \
    && rm -rf /app/backend /app/build \
    && mkdir -p /app/data /app/temp
COPY backend/app/ /app/app/

COPY scripts/ /app/scripts/
COPY backend/alembic/ /app/alembic/
COPY backend/alembic.ini /app/
COPY frontend/ /app/frontend/
RUN chmod +x /app/scripts/*.sh

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
