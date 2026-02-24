#!/bin/bash
# Build script for FFmpeg with optional PGO
set -e

OPUS_VERSION="${OPUS_VERSION:-1.6.1}"
SVT_AV1_VERSION="${SVT_AV1_VERSION:-4.0.1}"
BUILD_TYPE="${1:-}"  # "pgo-generate", "pgo-train", or "pgo-use"

# Determine architecture flags
# ARCH_FLAGS can be:
#   - unset: use -march=native (local builds)
#   - set to empty string: don't use any -march (multi-arch builds)
#   - set to specific value: use that value
if [ -z "${ARCH_FLAGS+x}" ]; then
    # ARCH_FLAGS is unset, default to native
    ARCH_FLAGS="-march=native"
fi
# If ARCH_FLAGS is set to empty string, we use no arch flags (generic build)
BASE_CFLAGS="${ARCH_FLAGS:+$ARCH_FLAGS }-O3 -flto -fomit-frame-pointer"
# Allow disabling LTO for faster CI builds (ENABLE_LTO=false)
ENABLE_LTO="${ENABLE_LTO:-true}"
if [ "$ENABLE_LTO" = "false" ]; then
    BASE_CFLAGS="${ARCH_FLAGS:+$ARCH_FLAGS }-O3 -fomit-frame-pointer"
    BASE_LDFLAGS="-Wl,-O3 -Wl,--gc-sections"
else
    BASE_CFLAGS="${ARCH_FLAGS:+$ARCH_FLAGS }-O3 -flto -fomit-frame-pointer"
    BASE_LDFLAGS="-Wl,-O3 -Wl,--gc-sections -flto"
fi
PGO_DIR="/build/profiles"

# Build Opus (only once, no PGO flags)
build_opus() {
    # Skip if already built
    if [ -f /usr/local/lib/libopus.a ]; then
        echo "=== Opus already built, skipping ==="
        return
    fi

    cd /build/opus-${OPUS_VERSION}
    make clean 2>/dev/null || true
    ./configure --prefix=/usr/local --enable-static --disable-shared \
        --disable-extra-programs --disable-doc CFLAGS="${BASE_CFLAGS}" LDFLAGS="${BASE_LDFLAGS}"
    echo "=== Building Opus ==="
    make -j$(nproc) install
}

# Build SVT-AV1 and FFmpeg with given flags
build_all() {
    local PFLAGS="$1"
    local CFLAGS="${BASE_CFLAGS} ${PFLAGS}"
    local LDFLAGS="${BASE_LDFLAGS} ${PFLAGS}"

    # Build SVT-AV1
    cd /build/SVT-AV1-v${SVT_AV1_VERSION}
    rm -rf Build && mkdir Build && cd Build
    cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_SHARED_LIBS=OFF -DCMAKE_C_FLAGS="$CFLAGS" -DCMAKE_CXX_FLAGS="$CFLAGS"
    echo "=== Building SVT-AV1 (${BUILD_TYPE:-standard}) ==="
    make -j$(nproc) SvtAv1Enc
    cp /build/SVT-AV1-v${SVT_AV1_VERSION}/Bin/Release/libSvtAv1Enc.a /usr/local/lib/
    cp ../Source/API/*.h /usr/local/include/

    # Create pkgconfig
    mkdir -p /usr/local/lib/pkgconfig
    printf '%s\n' "prefix=/usr/local" "exec_prefix=\${prefix}" "libdir=\${prefix}/lib" "includedir=\${prefix}/include" "" "Name: SvtAv1Enc" "Description: SVT-AV1 encoder" "Version: ${SVT_AV1_VERSION}" "Libs: -L\${libdir} -lSvtAv1Enc" "Libs.private: -lpthread -lm" "Cflags: -I\${includedir}" > /usr/local/lib/pkgconfig/SvtAv1Enc.pc
    pkg-config --exists SvtAv1Enc && echo "SvtAv1Enc found: $(pkg-config --modversion SvtAv1Enc)"

    # Build FFmpeg
    cd /build/FFmpeg
    make clean 2>/dev/null || true
    export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig"
    ./configure \
        --prefix=/usr/local --pkg-config-flags="--static" --extra-libs="-lpthread -lm" \
        --enable-lto --enable-gpl --disable-debug --disable-doc --disable-shared --enable-static \
        --disable-runtime-cpudetect --disable-autodetect --disable-programs \
        --enable-ffmpeg --enable-ffprobe \
        --enable-avcodec --enable-avformat --enable-avfilter \
        --enable-swresample --enable-protocol=file,pipe \
        --enable-demuxer=matroska,mov,mpegts --enable-muxer=matroska,null \
        --enable-decoder=h264,hevc,av1,aac,ac3,eac3,dca,truehd,mlp,pgssub \
        --enable-encoder=libsvtav1,libopus,pcm_s16le,wrapped_avframe \
        --enable-filter=cropdetect,crop,format,aformat,aresample,loudnorm \
        --enable-libsvtav1 --enable-libopus --enable-zlib \
        --extra-cflags="$CFLAGS -I/usr/local/include" \
        --extra-ldflags="$LDFLAGS -L/usr/local/lib"
    echo "=== Building FFmpeg (${BUILD_TYPE:-standard}) ==="
    make -j$(nproc) install
}

# Run PGO training
train_pgo() {
    echo "=== PGO Training ==="
    mkdir -p "$PGO_DIR"

    if ! ls /build/samples/*.mkv 2>/dev/null | head -1 > /dev/null; then
        echo "ERROR: PGO enabled but no sample videos found in /build/samples/"
        echo "Please provide sample videos for PGO training or set ENABLE_PGO=false"
        exit 1
    fi

    for f in /build/samples/*.mkv; do
        [ -f "$f" ] || continue
        echo "Training: $(basename "$f")"

        echo "  Stage: crop_detect"
        crop=$(ffmpeg -hide_banner -i "$f" -t 1 -vf cropdetect -an -f null - 2>&1 | grep -o 'crop=[0-9:]*' | tail -1)
        if [ -z "$crop" ]; then
            echo "ERROR: Crop detection failed"
            echo "Ensure sample videos are at least 10 seconds long and have valid video streams"
            exit 1
        fi
        echo "    Detected: $crop"

        echo "  Stage: audio_measure"
        json=$(ffmpeg -hide_banner -i "$f" -t 10 \
            -af "aformat=channel_layouts=stereo,loudnorm=I=-20:TP=-2:LRA=13:linear=true:print_format=json" \
            -vn -f null - 2>&1 | grep -A20 'input_i')

        i=$(echo "$json" | grep 'input_i' | sed 's/.*: "\([^"]*\)".*/\1/')
        tp=$(echo "$json" | grep 'input_tp' | sed 's/.*: "\([^"]*\)".*/\1/')
        lra=$(echo "$json" | grep 'input_lra' | sed 's/.*: "\([^"]*\)".*/\1/')
        thresh=$(echo "$json" | grep 'input_thresh' | sed 's/.*: "\([^"]*\)".*/\1/')
        offset=$(echo "$json" | grep 'target_offset' | sed 's/.*: "\([^"]*\)".*/\1/')

        if [ -z "$i" ] || [ -z "$tp" ] || [ -z "$lra" ] || [ -z "$thresh" ] || [ -z "$offset" ]; then
            echo "ERROR: Loudnorm measurement failed"
            exit 1
        fi
        echo "    Measured: I=${i} LUFS, TP=${tp} dBTP, LRA=${lra} LU"

        echo "  Stage: encoding"
        ffmpeg -hide_banner -i "$f" -t 15 \
            -vf "$crop,format=yuv420p10le" \
            -af "aformat=channel_layouts=stereo,loudnorm=I=-20:TP=-2:LRA=13:linear=true:measured_I=${i}:measured_TP=${tp}:measured_LRA=${lra}:measured_thresh=${thresh}:offset=${offset}" \
            -c:v libsvtav1 -preset 4 -crf 26 -g 225 -svtav1-params "tune=0:film-grain=8" \
            -c:a libopus -b:a 96k -f matroska -y /dev/null || { echo "ERROR: Encoding failed"; exit 1; }
    done
    echo "Profiles: $(find "$PGO_DIR" -name '*.gcda' 2>/dev/null | wc -l)"
}

# Main logic
case "$BUILD_TYPE" in
    "pgo-generate")
        build_opus
        build_all "-fprofile-generate=$PGO_DIR -fprofile-update=atomic"
        ;;
    "pgo-train")
        train_pgo
        ;;
    "pgo-use")
        build_all "-fprofile-use=$PGO_DIR -fprofile-correction -Wno-missing-profile"
        ;;
    "standard")
        build_opus
        build_all ""
        ;;
    *)
        echo "Usage: $0 {pgo-generate|pgo-train|pgo-use|standard}"
        exit 1
        ;;
esac
