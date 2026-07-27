#!/bin/bash
# Build script for FFmpeg with optional PGO
set -e

OPUS_VERSION="${OPUS_VERSION:-1.6.1}"
SVT_AV1_VERSION="${SVT_AV1_VERSION:-4.2.0}"
BUILD_TYPE="${1:-}"  # "pgo-generate", "pgo-train", or "pgo-use"
PREFERRED_AUDIO_LANGUAGES="${PREFERRED_AUDIO_LANGUAGES:-ger,deu,de,eng,en}"

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
echo "=== ENABLE_LTO=${ENABLE_LTO} ==="
if [ "$ENABLE_LTO" = "false" ]; then
    BASE_CFLAGS="${ARCH_FLAGS:+$ARCH_FLAGS }-O3 -fomit-frame-pointer"
    BASE_LDFLAGS="-Wl,-O3 -Wl,--gc-sections"
else
    BASE_CFLAGS="${ARCH_FLAGS:+$ARCH_FLAGS }-O3 -flto -fomit-frame-pointer"
    BASE_LDFLAGS="-Wl,-O3 -Wl,--gc-sections -flto"
fi
PGO_DIR="/build/profiles"

find_preferred_stream() {
    local streams="$1"
    local languages="$2"

    awk -F',' -v languages="$languages" '
        BEGIN {
            count = split(languages, preferred, ",")
            for (i = 1; i <= count; i++) {
                gsub(/^[ \t]+|[ \t]+$/, "", preferred[i])
                preferred[i] = tolower(preferred[i])
            }
        }
        {
            stream_language = tolower($2)
            for (i = 1; i <= count; i++) {
                if (stream_language == preferred[i]) {
                    print $1
                    exit
                }
            }
        }
    ' <<< "$streams"
}

first_stream() {
    awk -F',' 'NF && $1 != "" { print $1; exit }' <<< "$1"
}

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
    # Only pin to compile-time CPU flags when targeting a specific arch (-march set).
    # Generic builds (no ARCH_FLAGS) need runtime cpudetect so decode/swscale can
    # still pick AVX2 etc. on capable hosts instead of being stuck on the baseline.
    cpudetect_flag="--enable-runtime-cpudetect"
    [[ -n "$ARCH_FLAGS" ]] && cpudetect_flag="--disable-runtime-cpudetect"
    ./configure \
        --prefix=/usr/local --pkg-config-flags="--static" --extra-libs="-lpthread -lm" \
        --cc="${CC:-gcc}" --cxx="${CXX:-g++}" \
        --enable-lto --enable-gpl --disable-debug --disable-doc --disable-shared --enable-static \
        "$cpudetect_flag" --disable-autodetect --disable-programs \
        --disable-everything \
        --enable-ffmpeg --enable-ffprobe \
        --enable-avcodec --enable-avformat --enable-avfilter \
        --enable-swresample --enable-protocol=file,pipe \
        --enable-demuxer=matroska,mov --enable-muxer=matroska,null \
        --enable-decoder=h264,hevc,av1,aac,ac3,eac3,dca,truehd,mlp,pgssub,movtext \
        --enable-encoder=libsvtav1,libopus,pcm_s16le,wrapped_avframe,srt \
        --enable-parser=h264,hevc,av1,aac,ac3,dca,mlp \
        --enable-bsf=extract_extradata,av1_metadata,h264_mp4toannexb,hevc_mp4toannexb \
        --enable-filter=cropdetect,crop,scale,format,aformat,aresample,loudnorm,showinfo,null,anull \
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
        echo "WARNING: PGO enabled but no sample videos found in /build/samples/"
        echo "Skipping PGO training; the build will fall back to a standard (non-PGO) build"
        return
    fi

    for f in /build/samples/*.mkv; do
        [ -f "$f" ] || continue
        basename_f=$(basename "$f")
        echo "Training: $basename_f"

        # Map sample filename prefix to preset params (kept in sync with
        # BUILTIN_PRESETS in backend/app/services/lifecycle.py)
        base_svt="tune=1:enable-variance-boost=1:tf-strength=1:sharpness=1:enable-restoration=1:enable-qm=1:qm-min=0:qm-max=15:chroma-qm-min=8:chroma-qm-max=15"
        # No variance-boost/tf-strength: not worth their bitrate cost on animated content.
        animated_svt="tune=1:sharpness=1:enable-restoration=1:enable-qm=1:qm-min=0:qm-max=15:chroma-qm-min=8:chroma-qm-max=15"
        preset_crf=26
        svt_base="$base_svt"
        case "$basename_f" in
            animated_*)
                preset_crf=35
                svt_base="$animated_svt"
                echo "    Preset: animated (CRF $preset_crf, $svt_base)"
                ;;
            grainy_*)
                preset_crf=26
                svt_base="${base_svt}:film-grain=12:film-grain-denoise=1"
                echo "    Preset: grainy (CRF $preset_crf, $svt_base)"
                ;;
            verygrainy_*)
                preset_crf=26
                svt_base="${base_svt}:film-grain=18:film-grain-denoise=1"
                echo "    Preset: verygrainy (CRF $preset_crf, $svt_base)"
                ;;
            *)
                echo "    Preset: default (CRF $preset_crf, $svt_base)"
                ;;
        esac

        audio_streams=$(ffprobe -v error -select_streams a -show_entries stream=index:stream_tags=language -of csv=p=0 "$f")
        preferred_audio=$(find_preferred_stream "$audio_streams" "$PREFERRED_AUDIO_LANGUAGES")
        first_audio=$(first_stream "$audio_streams")
        audio_idx="${preferred_audio:-$first_audio}"
        if [ -z "$audio_idx" ]; then
            echo "ERROR: No audio streams found"
            exit 1
        fi
        echo "    Audio: stream $audio_idx"

        echo "  Stage: crop_detect"
        crop=$(ffmpeg -hide_banner -i "$f" -t 1 -vf cropdetect=round=4 -an -f null - 2>&1 | grep -o 'crop=[0-9:]*' | tail -1)
        if [ -z "$crop" ]; then
            echo "ERROR: Crop detection failed"
            echo "Ensure sample videos are at least 10 seconds long and have valid video streams"
            exit 1
        fi
        echo "    Detected: $crop"

        echo "  Stage: audio_measure"
        json=$(ffmpeg -hide_banner -i "$f" -map 0:$audio_idx -t 10 \
            -af "aformat=channel_layouts=stereo,loudnorm=I=-20:TP=-2:LRA=13:linear=true:print_format=json" \
            -vn -sn -dn -f null - 2>&1 | grep -A20 'input_i')

        i=$(echo "$json" | grep 'input_i' | head -1 | sed 's/.*: "\([^"]*\)".*/\1/')
        tp=$(echo "$json" | grep 'input_tp' | head -1 | sed 's/.*: "\([^"]*\)".*/\1/')
        lra=$(echo "$json" | grep 'input_lra' | head -1 | sed 's/.*: "\([^"]*\)".*/\1/')
        thresh=$(echo "$json" | grep 'input_thresh' | head -1 | sed 's/.*: "\([^"]*\)".*/\1/')
        offset=$(echo "$json" | grep 'target_offset' | head -1 | sed 's/.*: "\([^"]*\)".*/\1/')

        if [ -z "$i" ] || [ -z "$tp" ] || [ -z "$lra" ] || [ -z "$thresh" ] || [ -z "$offset" ]; then
            echo "ERROR: Loudnorm measurement failed"
            exit 1
        fi
        echo "    Measured: I=${i} LUFS, TP=${tp} dBTP, LRA=${lra} LU"

        # Detect source dimensions for resolution downscale training (default: 1080p cap)
        src_res=$(ffprobe -v error -select_streams v:0 \
            -show_entries stream=width,height -of csv=p=0 "$f")
        src_width=$(echo "$src_res" | cut -d',' -f1)
        src_height=$(echo "$src_res" | cut -d',' -f2)
        vf_chain="$crop,format=yuv420p10le"
        if [ -n "$src_width" ] && [ -n "$src_height" ] && { [ "$src_width" -gt 1920 ] || [ "$src_height" -gt 1080 ]; }; then
            vf_chain="$crop,scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p10le"
            echo "    Scale: ${src_width}x${src_height} -> fit 1920x1080"
        fi

        # Detect HDR for color flag training
        color_transfer=$(ffprobe -v error -select_streams v:0 \
            -show_entries stream=color_transfer -of csv=p=0 "$f")
        color_flags=""
        svt_hdr=""
        if [ "$color_transfer" = "smpte2084" ]; then
            color_flags="-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -color_range tv"
            svt_hdr=":color-primaries=9:transfer-characteristics=16:matrix-coefficients=9"
            echo "    HDR: PQ/HDR10 detected"

            # Mirrors conversion_wrapper.sh's mastering-display/content-light extraction.
            hdr_side_data=$(ffprobe -v quiet -select_streams v:0 \
                -show_frames -read_intervals "%+#1" \
                -print_format json "$f" 2>/dev/null)

            if [ -n "$hdr_side_data" ]; then
                extract_frac() { echo "$hdr_side_data" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([0-9]*\/[0-9]*\)\".*/\1/p" | head -1 | awk -F'/' '{printf "%.4f", $1/$2}'; }
                red_x=$(extract_frac red_x)
                red_y=$(extract_frac red_y)
                green_x=$(extract_frac green_x)
                green_y=$(extract_frac green_y)
                blue_x=$(extract_frac blue_x)
                blue_y=$(extract_frac blue_y)
                white_x=$(extract_frac white_point_x)
                white_y=$(extract_frac white_point_y)
                min_lum=$(extract_frac min_luminance)
                max_lum=$(extract_frac max_luminance)

                if [ -n "$green_x" ] && [ -n "$green_y" ] && [ -n "$blue_x" ] && [ -n "$blue_y" ] && \
                   [ -n "$red_x" ] && [ -n "$red_y" ] && [ -n "$white_x" ] && [ -n "$white_y" ] && \
                   [ -n "$max_lum" ] && [ -n "$min_lum" ]; then
                    mastering_display="G(${green_x},${green_y})B(${blue_x},${blue_y})R(${red_x},${red_y})WP(${white_x},${white_y})L(${max_lum},${min_lum})"
                    svt_hdr="${svt_hdr}:mastering-display=${mastering_display}"
                    echo "    Mastering display: $mastering_display"
                fi

                max_cll=$(echo "$hdr_side_data" | sed -n 's/.*"max_content"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)
                max_fall=$(echo "$hdr_side_data" | sed -n 's/.*"max_average"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)

                if [ -n "$max_cll" ] && [ -n "$max_fall" ]; then
                    content_light="${max_cll},${max_fall}"
                    svt_hdr="${svt_hdr}:content-light=${content_light}"
                    echo "    Content light level: MaxCLL=$max_cll, MaxFALL=$max_fall"
                fi
            fi
        elif [ "$color_transfer" = "arib-std-b67" ]; then
            color_flags="-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc -color_range tv"
            svt_hdr=":color-primaries=9:transfer-characteristics=18:matrix-coefficients=9"
            echo "    HDR: HLG detected"
        fi

        # luminance-qp-bias: applies to SDR/HLG, excluded for PQ/HDR10.
        if [ "$color_transfer" != "smpte2084" ]; then
            svt_hdr="${svt_hdr}:luminance-qp-bias=10"
        fi

        # Two invocations to mirror the video/audio branch split at runtime.
        # Kept serial - concurrent writers would race on .gcda merge.
        echo "  Stage: encoding (video)"
        ffmpeg -hide_banner -i "$f" -map 0:v:0 -an -sn -dn -t 15 \
            -vf "$vf_chain" \
            -c:v libsvtav1 -preset 4 -crf $preset_crf -g 225 -svtav1-params "${svt_base}${svt_hdr}" \
            $color_flags \
            -f matroska -y /dev/null || { echo "ERROR: Video encoding failed"; exit 1; }

        echo "  Stage: encoding (audio)"
        ffmpeg -hide_banner -i "$f" -map 0:$audio_idx -vn -sn -dn -t 15 \
            -af "aformat=channel_layouts=stereo,loudnorm=I=-20:TP=-2:LRA=13:linear=true:measured_I=${i}:measured_TP=${tp}:measured_LRA=${lra}:measured_thresh=${thresh}:offset=${offset}" \
            -c:a libopus -b:a 96k -f matroska -y /dev/null || { echo "ERROR: Audio encoding failed"; exit 1; }
    done
    echo "Profiles: $(find "$PGO_DIR" -name '*.gcda' 2>/dev/null | wc -l)"
}

# Main logic
case "$BUILD_TYPE" in
    "pgo-generate")
        build_opus
        build_all "-fprofile-generate=$PGO_DIR -fprofile-update=prefer-atomic"
        ;;
    "pgo-train")
        train_pgo
        ;;
    "pgo-use")
        if ls "$PGO_DIR"/*.gcda >/dev/null 2>&1; then
            build_all "-fprofile-use=$PGO_DIR -fprofile-partial-training"
        else
            echo "WARNING: No PGO profile data found in $PGO_DIR, falling back to standard build"
            build_all ""
        fi
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
