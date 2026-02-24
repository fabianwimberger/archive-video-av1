# syntax=docker/dockerfile:1
FROM ubuntu:25.10 AS builder

ARG OPUS_VERSION="1.6.1"
ARG SVT_AV1_VERSION="4.0.1"
ARG ENABLE_PGO="false"
ARG TARGETARCH

ENV PGO_DIR="/build/profiles"
ENV ARCH_FLAGS=""

RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    build-essential cmake nasm pkg-config \
    wget ca-certificates tar xz-utils git \
    autoconf automake libtool zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Download sources (cached unless versions change)
RUN wget -q "https://downloads.xiph.org/releases/opus/opus-${OPUS_VERSION}.tar.gz" && \
    tar -xzf "opus-${OPUS_VERSION}.tar.gz" && rm "opus-${OPUS_VERSION}.tar.gz" && \
    wget -q "https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v${SVT_AV1_VERSION}/SVT-AV1-v${SVT_AV1_VERSION}.tar.gz" && \
    tar -xzf SVT-AV1-v${SVT_AV1_VERSION}.tar.gz && rm SVT-AV1-v${SVT_AV1_VERSION}.tar.gz && \
    git clone --depth 1 https://git.ffmpeg.org/ffmpeg.git FFmpeg

# Copy build script (cached unless script changes)
COPY scripts/build.sh /build/build.sh
RUN chmod +x /build/build.sh

# Copy samples (triggers PGO rebuild when samples change)
# Note: Put this AFTER the build script so changing samples doesn't invalidate earlier layers
COPY sample/ /build/samples/

# Set architecture flags for multi-arch builds
# For amd64: use generic x86-64-v2 (baseline for modern x86_64)
# For arm64: use generic armv8-a
RUN if [ "$TARGETARCH" = "amd64" ]; then \
        echo "ARCH_FLAGS=-march=x86-64-v2" > /build/arch_flags.txt; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
        echo "ARCH_FLAGS=-march=armv8-a" > /build/arch_flags.txt; \
    else \
        echo "ARCH_FLAGS=" > /build/arch_flags.txt; \
    fi

# Build with PGO
# Layer 1: Build Opus and FFmpeg with -fprofile-generate (cached if sources/script unchanged)
RUN export ARCH_FLAGS=$(cat /build/arch_flags.txt | cut -d= -f2) && \
    if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-generate; \
    fi

# Layer 2: Run PGO training (rebuilds if samples change)
RUN if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-train; \
    fi

# Layer 3: Rebuild FFmpeg with -fprofile-use (rebuilds if training/profiles change)
RUN export ARCH_FLAGS=$(cat /build/arch_flags.txt | cut -d= -f2) && \
    if [ "$ENABLE_PGO" = "true" ]; then \
        /build/build.sh pgo-use; \
    else \
        /build/build.sh standard; \
    fi

# Verification and stripping (always runs after successful build)
RUN echo "=== Verifying optimizations ==="; \
    \
    if ! nm /usr/local/bin/ffmpeg 2>/dev/null | grep -q "__gnu_lto"; then \
        echo "WARNING: No LTO symbols found in ffmpeg binary"; \
    else \
        echo "✓ LTO detected in ffmpeg"; \
    fi; \
    \
    if [ "$ENABLE_PGO" = "true" ]; then \
        profile_count=$(find "$PGO_DIR" -name '*.gcda' 2>/dev/null | wc -l); \
        if [ "$profile_count" -lt 10 ]; then \
            echo "ERROR: PGO was enabled but only $profile_count profile files were generated (expected at least 10)"; \
            echo "This indicates PGO training failed or samples were insufficient"; \
            exit 1; \
        fi; \
        echo "✓ PGO profiles: $profile_count .gcda files found"; \
    fi; \
    \
    if ! strings /usr/local/bin/ffmpeg 2>/dev/null | grep -q "GCC"; then \
        echo "WARNING: Unable to verify compiler in binary"; \
    fi; \
    \
    echo "=== Stripping binaries ==="; \
    strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe || { echo "ERROR: Failed to strip binaries"; exit 1; }

# Runtime stage
FROM ubuntu:25.10
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/venv/bin:$PATH"
WORKDIR /app

COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

# Install Python runtime dependencies and add license notices
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
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
COPY frontend/ /app/frontend/
RUN chmod +x /app/scripts/*.sh && python3 scripts/download_vendors.py

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
