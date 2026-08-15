# syntax=docker/dockerfile:1
FROM ubuntu:26.10 AS builder

ARG FFMPEG_VERSION="9.0.1"
ARG OPUS_VERSION="1.6.1"
ARG SVT_AV1_VERSION="4.2.0"
ARG ENABLE_PGO="false"
ARG ENABLE_LTO="true"
ARG ARCH_FLAGS

# Pinned checksums for the source tarballs below; update alongside the
# matching *_VERSION when bumping.
ARG FFMPEG_SHA256="657dbf258cce6c0681714b6e40b8ca69e988c67ecfc2f7aab47574e6ebaceb38"
ARG OPUS_SHA256="6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1"
ARG SVT_AV1_SHA256="c7b13c4a84bd3751aa35fcc72be13e6875467e7c2216879251a486e5b1e4e740"

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

# Download sources (cached unless versions change)
RUN wget -q "https://downloads.xiph.org/releases/opus/opus-${OPUS_VERSION}.tar.gz" && \
    echo "${OPUS_SHA256}  opus-${OPUS_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf "opus-${OPUS_VERSION}.tar.gz" && rm "opus-${OPUS_VERSION}.tar.gz" && \
    wget -q "https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v${SVT_AV1_VERSION}/SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" && \
    echo "${SVT_AV1_SHA256}  SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf SVT-AV1-v${SVT_AV1_VERSION}.tar.gz && rm SVT-AV1-v${SVT_AV1_VERSION}.tar.gz && \
    wget -q "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.gz" && \
    echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.gz" | sha256sum -c - && \
    tar -xzf "ffmpeg-${FFMPEG_VERSION}.tar.gz" && rm "ffmpeg-${FFMPEG_VERSION}.tar.gz" && \
    mv "ffmpeg-${FFMPEG_VERSION}" FFmpeg

# Copy build script (cached unless script changes)
COPY scripts/build.sh /build/build.sh
RUN chmod +x /build/build.sh

# ARCH_FLAGS is passed via build-arg:
# - GitHub builds: not set (empty = generic, no -march flag)
# - Local builds (Makefile): set to -march=native
# Build script handles unset vs empty string differently

# Build with PGO
# Layer 1: Build Opus and FFmpeg with -fprofile-generate (cached if sources/script unchanged)
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-generate; \
    fi

# Copy samples after Layer 1, so a sample change doesn't invalidate it too.
COPY sample/ /build/samples/

# Layer 2: Run PGO training (rebuilds if samples change)
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-train; \
    fi

# Layer 3: Rebuild FFmpeg with -fprofile-use (rebuilds if training/profiles change)
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-use; \
    else \
        /build/build.sh standard; \
    fi

# Verification and stripping (always runs after successful build)
RUN echo "=== Verifying optimizations ==="; \
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
            echo "✓ PGO profiles: $profile_count .gcda files found"; \
        fi; \
    fi; \
    \
    if ! strings /usr/local/bin/ffmpeg 2>/dev/null | grep -q "GCC"; then \
        echo "WARNING: Unable to verify compiler in binary"; \
    fi; \
    \
    echo "=== Stripping binaries ==="; \
    strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe || { echo "ERROR: Failed to strip binaries"; exit 1; }

# Runtime stage
FROM ubuntu:26.10
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/venv/bin:$PATH" \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive
WORKDIR /app

COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

# Install Python runtime dependencies and add license notices
RUN apt-get update -qq && apt-get upgrade -y -qq && apt-get install -y -qq --no-install-recommends \
    python3 python3-venv ca-certificates mkvtoolnix bash \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/share/licenses \
    && echo "================================================================================" > /usr/share/licenses/FFmpeg-LICENSE \
    && echo "FFmpeg License Notice" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "================================================================================" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "This software uses FFmpeg (https://ffmpeg.org/), which is licensed under" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "the GNU General Public License version 2 or later (GPL v2+)." >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "FFmpeg source code can be obtained from:" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "  https://git.ffmpeg.org/ffmpeg.git" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "The full GPL v2 license text is available at:" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "  https://www.gnu.org/licenses/old-licenses/gpl-2.0.html" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "This Docker image also includes:" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "  - SVT-AV1 (BSD-3-Clause): https://gitlab.com/AOMediaCodec/SVT-AV1" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "  - Opus (BSD-3-Clause): https://opus-codec.org/" >> /usr/share/licenses/FFmpeg-LICENSE \
    && echo "================================================================================" >> /usr/share/licenses/FFmpeg-LICENSE

COPY backend/requirements.txt .
RUN python3 -m venv /app/venv \
    && pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /app/data /app/temp

COPY scripts/ /app/scripts/
COPY backend/app/ /app/app/
COPY backend/alembic/ /app/alembic/
COPY backend/alembic.ini /app/
COPY frontend/ /app/frontend/
RUN chmod +x /app/scripts/*.sh && python3 scripts/download_vendors.py

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
