#!/bin/bash
# Single-file conversion wrapper for web service
# Outputs structured progress to stdout for Python parser

INPUT_FILE="$1"
OUTPUT_FILE="$2"
CRF="$3"
PRESET="$4"
SVT_PARAMS="$5"
AUDIO_BITRATE="$6"
SKIP_CROP="$7"
MAX_HEIGHT="${8:-1080}"

TEMP_DIR="${TEMP_DIR:-/app/temp}"
AUDIO_TRACK_MODE="${AUDIO_TRACK_MODE:-preferred}"
SUBTITLE_TRACK_MODE="${SUBTITLE_TRACK_MODE:-preferred}"
PREFERRED_AUDIO_LANGUAGES="${PREFERRED_AUDIO_LANGUAGES:-ger,deu,de,eng,en}"
PREFERRED_SUBTITLE_LANGUAGES="${PREFERRED_SUBTITLE_LANGUAGES:-ger,deu,de,eng,en}"

# --- TRAP SIGNALS ---

# pkill -P $$ would miss branch A's ffmpeg (a backgrounded subshell's child,
# not a direct child), so kill the whole process group instead.
kill_all() {
    pkill -g $$ 2>/dev/null
}

cleanup() {
    # kill_all's pkill -g $$ also signals this script's own PID; without
    # disarming the trap first, that self-signal re-enters this handler.
    trap '' SIGTERM SIGINT
    echo "STATUS:Stopping conversion..."
    kill_all

    # Clean up temp files
    for f in "$tmp_video" "$tmp_audio" "$audio_log" "$AUDIO_CMD_FILE" "$LOUDNORM_JSON" "$TAGS_XML"; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f"
    done

    exit 1
}
trap cleanup SIGTERM SIGINT

echo "STAGE:initializing"

# --- HELPER FUNCTIONS (from original script) ---

# Helpers read from $PROBE (one ffprobe -of flat dump) instead of re-probing
# the container per field.

# probe_get <escaped-key>: value for a flat "key=value" line (quotes stripped).
# Keys are matched as sed BRE patterns, so callers must escape literal dots.
probe_get() {
    local val
    val=$(sed -n "s/^$1=//p" <<< "$PROBE" | head -1)
    val="${val%\"}"
    val="${val#\"}"
    echo "$val"
}

# probe_ordinals <codec_type>: stream ordinals (== absolute stream index for
# well-formed containers) matching a codec_type, one per line.
probe_ordinals() {
    sed -n "s/^streams\.stream\.\([0-9]\{1,\}\)\.codec_type=\"$1\"\$/\1/p" <<< "$PROBE"
}

# probe_field <ordinal> <field>: a single stream field.
probe_field() {
    probe_get "streams\.stream\.$1\.$2"
}

# probe_stream_list <codec_type>: "index,language" per line (matches the old
# `-of csv=p=0` shape). Reads the stream's own "index" field rather than
# $ord, since $ord only equals the absolute stream index as long as the
# probe doesn't use -select_streams.
probe_stream_list() {
    local ord idx lang
    for ord in $(probe_ordinals "$1"); do
        idx=$(probe_field "$ord" index)
        lang=$(probe_get "streams\.stream\.${ord}\.tags\.language")
        echo "${idx},${lang}"
    done
}

get_total_frames() {
    local ord="$1"
    local frames duration fps

    frames=$(probe_field "$ord" nb_frames)

    if [[ ! "$frames" =~ ^[0-9]+$ ]]; then
        duration=$(probe_get 'format\.duration')
        fps=$(probe_field "$ord" r_frame_rate)

        if [[ -n "$duration" && -n "$fps" && "$duration" != "N/A" ]]; then
            frames=$(awk -v d="$duration" -v f="$fps" 'BEGIN { split(f,a,"/"); rate=(a[2]>0)?a[1]/a[2]:0; printf "%.0f", d*rate }')
        fi
    fi
    echo "${frames:-0}"
}

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

# probe_ordinal_for_index <codec_type> <index>: ordinal for a stream index
# returned by find_preferred_stream/first_stream, for feeding into probe_field.
probe_ordinal_for_index() {
    local ord
    for ord in $(probe_ordinals "$1"); do
        [[ "$(probe_field "$ord" index)" == "$2" ]] && { echo "$ord"; return; }
    done
}


# --- MAIN CONVERSION LOGIC ---

# Encode to TEMP_DIR (fast local storage) when available; mkvmerge remuxes
# into OUTPUT_FILE afterwards, so the two don't need to share a filesystem.
output_dir="$(dirname "$OUTPUT_FILE")"
if [[ -d "$TEMP_DIR" && -w "$TEMP_DIR" ]]; then
    temp_dir="$TEMP_DIR"
else
    temp_dir="$output_dir"
fi
tmp_video="${temp_dir}/.$(basename "$OUTPUT_FILE").video.tmp"
tmp_audio="${temp_dir}/.$(basename "$OUTPUT_FILE").audio.tmp"
audio_log="${temp_dir}/.$(basename "$OUTPUT_FILE").audio.log"
AUDIO_CMD_FILE="${temp_dir}/.$(basename "$OUTPUT_FILE").audio.cmd"

# One ffprobe call for everything below, instead of once per field.
PROBE=$(ffprobe -v error \
    -show_entries "format=duration,start_time:stream=index,codec_type,codec_name,width,height,nb_frames,r_frame_rate,start_time,color_transfer,color_primaries,color_space:stream_tags=language" \
    -of flat "$INPUT_FILE" 2>/dev/null)

if [[ -z "$PROBE" ]]; then
    echo "ERROR:Failed to probe input file"
    exit 1
fi

video_ord=$(probe_ordinals video | head -1)
if [[ -z "$video_ord" ]]; then
    echo "ERROR:No video stream found"
    exit 1
fi

# Get total frames for progress calculation
TOTAL_FRAMES=$(get_total_frames "$video_ord")
echo "total_frames=$TOTAL_FRAMES"

# Detect video codec
video_codec=$(probe_field "$video_ord" codec_name)
is_av1=0
[[ "$video_codec" == "av1" ]] && is_av1=1
echo "STATUS:Detected video codec: $video_codec"

# --- HDR DETECTION ---
color_transfer=$(probe_field "$video_ord" color_transfer)
color_primaries=$(probe_field "$video_ord" color_primaries)
color_space=$(probe_field "$video_ord" color_space)

is_hdr=0
hdr_type=""
tc_value=""
color_trc_name=""

if [[ "$color_transfer" == "smpte2084" ]]; then
    is_hdr=1
    hdr_type="HDR10"
    tc_value=16
    color_trc_name="smpte2084"
elif [[ "$color_transfer" == "arib-std-b67" ]]; then
    is_hdr=1
    hdr_type="HLG"
    tc_value=18
    color_trc_name="arib-std-b67"
fi

# Check for Dolby Vision RPU side data
dv_profile=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream_side_data=dv_profile \
    -of csv=p=0 "$INPUT_FILE" 2>/dev/null | head -1)

if [[ -n "$dv_profile" ]]; then
    echo "STATUS:Dolby Vision profile $dv_profile detected; RPU will be discarded, output color may not match the source"
    if [[ -z "$tc_value" ]]; then
        # Untagged base layer (e.g. profile 5) - leave metadata as detected.
        :
    else
        is_hdr=1
        hdr_type="DV"
    fi
fi

if [[ $is_hdr -eq 1 ]]; then
    echo "STATUS:HDR detected: $hdr_type (transfer=$color_transfer, primaries=$color_primaries)"
fi

# --- HDR METADATA EXTRACTION ---
mastering_display=""
content_light=""

if [[ $is_hdr -eq 1 && "$color_transfer" == "smpte2084" ]]; then
    echo "STATUS:Extracting HDR10 static metadata..."
    # Use -show_frames (not -show_entries frame=side_data_list) to get populated side data
    hdr_side_data=$(ffprobe -v quiet -select_streams v:0 \
        -show_frames -read_intervals "%+#1" \
        -print_format json "$INPUT_FILE" 2>/dev/null)

    if [[ -n "$hdr_side_data" ]]; then
        # Values are fractions like "34000/50000" - divide to get SVT-AV1's format.
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

        if [[ -n "$green_x" && -n "$green_y" && -n "$blue_x" && -n "$blue_y" && -n "$red_x" && -n "$red_y" && -n "$white_x" && -n "$white_y" && -n "$max_lum" && -n "$min_lum" ]]; then
            mastering_display="G(${green_x},${green_y})B(${blue_x},${blue_y})R(${red_x},${red_y})WP(${white_x},${white_y})L(${max_lum},${min_lum})"
            echo "STATUS:Mastering display: $mastering_display"
        fi

        # Extract content light level (unquoted integers in JSON)
        max_cll=$(echo "$hdr_side_data" | sed -n 's/.*"max_content"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)
        max_fall=$(echo "$hdr_side_data" | sed -n 's/.*"max_average"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)

        if [[ -n "$max_cll" && -n "$max_fall" ]]; then
            content_light="${max_cll},${max_fall}"
            echo "STATUS:Content light level: MaxCLL=$max_cll, MaxFALL=$max_fall"
        fi
    fi
fi

# Detect crop
crop=""
if [[ $is_av1 -eq 0 && $SKIP_CROP -eq 0 ]]; then
    echo "STAGE:crop_detect"
    echo "STATUS:Detecting crop parameters..."

    # Get video duration for percentage-based sampling
    duration=$(probe_get 'format\.duration')

    if [[ -n "$duration" && "$duration" != "N/A" ]]; then
        # Get original resolution
        orig_width=$(probe_field "$video_ord" width)
        orig_height=$(probe_field "$video_ord" height)

        # Collect crop values by sampling at 8 points
        all_crops=""
        for percent in 10 20 30 40 50 60 70 80; do
            time=$(awk -v d="$duration" -v p="$percent" 'BEGIN { printf "%.0f", d * p / 100 }')

            # Run cropdetect - filter analysis to null output
            crop_value=$(ffmpeg -hide_banner -ss $time -i "$INPUT_FILE" -t 3 -vf cropdetect=round=4 -an -f null - 2>&1 | grep -o 'crop=[0-9:]*' | tail -1)

            if [[ -n "$crop_value" ]]; then
                # Reject asymmetric bars - a real letterbox/pillarbox is centered
                symmetric=$(echo "$crop_value" | awk -F'[=:]' -v ow="$orig_width" -v oh="$orig_height" '{
                    w=$2; h=$3; x=$4; y=$5
                    dx = x - (ow - w - x); if (dx < 0) dx = -dx
                    dy = y - (oh - h - y); if (dy < 0) dy = -dy
                    print (dx <= 8 && dy <= 8) ? "yes" : "no"
                }')

                if [[ "$symmetric" == "yes" ]]; then
                    echo "STATUS:Sample ${percent}% (@${time}s): ${crop_value}"
                    all_crops="${all_crops}${crop_value}"$'\n'
                else
                    echo "STATUS:Sample ${percent}% (@${time}s): ${crop_value} (rejected, asymmetric - likely a dark scene)"
                fi
            else
                echo "STATUS:Sample ${percent}% (@${time}s): none"
            fi
        done

        # Find consensus: require 2 or more matching symmetric samples
        consensus=$(echo "$all_crops" | grep -v '^$' | sort | uniq -c | sort -rn | head -1)
        consensus_count=$(echo "$consensus" | awk '{print $1}')
        crop=$(echo "$consensus" | awk '{if ($1 >= 2) print $2}')

        echo "STATUS:Consensus: ${consensus_count:-0} matches for $(echo "$consensus" | awk '{print $2}')"

        if [[ -n "$crop" ]]; then
            crop_width=$(echo "$crop" | cut -d'=' -f2 | cut -d':' -f1)
            crop_height=$(echo "$crop" | cut -d'=' -f2 | cut -d':' -f2)

            # Skip crop if dimensions match original (no-op)
            if [[ "$crop_width" == "$orig_width" && "$crop_height" == "$orig_height" ]]; then
                echo "STATUS:No crop needed, dimensions unchanged (${orig_width}x${orig_height})"
                crop=""
            else
                echo "STATUS:Applying crop ${orig_width}x${orig_height} -> ${crop_width}x${crop_height} ($crop)"
            fi
        else
            echo "STATUS:No crop detected, encoding at original resolution"
        fi
    else
        echo "STATUS:Could not determine video duration, skipping crop detection"
    fi
fi

# --- VIDEO FILTER CHAIN ---
# Determine if downscaling is needed
# Map MAX_HEIGHT to bounding box (long edge x short edge)
case "$MAX_HEIGHT" in
    720)  MAX_WIDTH=1280 ;;
    1080) MAX_WIDTH=1920 ;;
    2160) MAX_WIDTH=3840 ;;
    *)    MAX_WIDTH=1920; MAX_HEIGHT=1080 ;;
esac

scale_filter=""
if [[ $is_av1 -eq 0 ]]; then
    # Get source dimensions (use post-crop if crop is applied)
    if [[ -n "$crop" ]]; then
        source_width=$(echo "$crop" | cut -d'=' -f2 | cut -d':' -f1)
        source_height=$(echo "$crop" | cut -d'=' -f2 | cut -d':' -f2)
    else
        source_width=$(probe_field "$video_ord" width)
        source_height=$(probe_field "$video_ord" height)
    fi

    if [[ -n "$source_width" && -n "$source_height" ]] && \
       [[ "$source_width" -gt "$MAX_WIDTH" || "$source_height" -gt "$MAX_HEIGHT" ]]; then
        scale_filter="scale=${MAX_WIDTH}:${MAX_HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        echo "STATUS:Downscaling ${source_width}x${source_height} to fit ${MAX_WIDTH}x${MAX_HEIGHT}"
    fi
fi

# Build video filter string (skip entirely when copying the video stream,
# since -vf is incompatible with -c:v copy)
vf_parts=""
[[ -n "$crop" ]] && vf_parts="$crop"
[[ -n "$scale_filter" ]] && { [[ -n "$vf_parts" ]] && vf_parts="${vf_parts},${scale_filter}" || vf_parts="$scale_filter"; }
if [[ $is_av1 -eq 0 ]]; then
    [[ -n "$vf_parts" ]] && vf_parts="${vf_parts},format=yuv420p10le" || vf_parts="format=yuv420p10le"
fi
vf=""
[[ -n "$vf_parts" ]] && vf="-vf $vf_parts"

# Detect audio/subs
audio_streams=$(probe_stream_list audio)
preferred_audio=$(find_preferred_stream "$audio_streams" "$PREFERRED_AUDIO_LANGUAGES")
first_audio=$(first_stream "$audio_streams")
audio_idx="${preferred_audio:-$first_audio}"

if [[ -z "$audio_idx" ]]; then
    echo "ERROR:No audio streams found"
    exit 1
fi

case "$AUDIO_TRACK_MODE" in
    preferred)
        audio_map="-map 0:$audio_idx"
        audio_indices=("$audio_idx")
        echo "STATUS:Audio track mode: preferred (stream $audio_idx)"
        ;;
    all)
        audio_map="-map 0:a"
        mapfile -t audio_indices <<< "$(awk -F',' 'NF && $1 != "" { print $1 }' <<< "$audio_streams")"
        echo "STATUS:Audio track mode: all (${#audio_indices[@]} track(s))"
        ;;
    *)
        echo "ERROR:Invalid AUDIO_TRACK_MODE '$AUDIO_TRACK_MODE' (expected preferred or all)"
        exit 1
        ;;
esac

subtitle_info=$(probe_stream_list subtitle)
sub_map=""
sub_codec="-c:s copy"
case "$SUBTITLE_TRACK_MODE" in
    preferred)
        preferred_sub=$(find_preferred_stream "$subtitle_info" "$PREFERRED_SUBTITLE_LANGUAGES")
        first_sub=$(first_stream "$subtitle_info")
        subtitle_idx="${preferred_sub:-$first_sub}"
        sub_ord=""
        [[ -n "$subtitle_idx" ]] && sub_ord=$(probe_ordinal_for_index subtitle "$subtitle_idx")
        [[ -n "$subtitle_idx" ]] && sub_map="-map 0:$subtitle_idx"
        echo "STATUS:Subtitle track mode: preferred${subtitle_idx:+ (stream $subtitle_idx)}"
        ;;
    all)
        sub_map="-map 0:s?"
        echo "STATUS:Subtitle track mode: all"
        ;;
    none)
        echo "STATUS:Subtitle track mode: none"
        ;;
    *)
        echo "ERROR:Invalid SUBTITLE_TRACK_MODE '$SUBTITLE_TRACK_MODE' (expected preferred, all, or none)"
        exit 1
        ;;
esac

# mov_text can't be copied into MKV; convert to srt. Checks the stream(s)
# actually selected above, not s:0, since language preference may differ.
if [[ "$SUBTITLE_TRACK_MODE" == "all" ]]; then
    if [[ -n "$sub_map" ]]; then
        has_mov_text=0
        has_other=0
        for sub_ord in $(probe_ordinals subtitle); do
            if [[ "$(probe_field "$sub_ord" codec_name)" == "mov_text" ]]; then
                has_mov_text=1
            else
                has_other=1
            fi
        done
        if [[ $has_mov_text -eq 1 && $has_other -eq 1 ]]; then
            echo "STATUS:Mixed subtitle codecs (mov_text and other) in 'all' mode; some subtitle tracks may fail to remux"
        elif [[ $has_mov_text -eq 1 ]]; then
            sub_codec="-c:s srt"
            echo "STATUS:Subtitle codec mov_text incompatible with MKV, converting to srt"
        fi
    fi
elif [[ -n "$sub_ord" ]]; then
    sub_codec_name=$(probe_field "$sub_ord" codec_name)
    if [[ "$sub_codec_name" == "mov_text" ]]; then
        sub_codec="-c:s srt"
        echo "STATUS:Subtitle codec mov_text incompatible with MKV, converting to srt"
    fi
fi

# Determine video encoding parameters
# Only copy if input is AV1 AND no crop/scale needed
needs_filter=0
[[ -n "$crop" || -n "$scale_filter" ]] && needs_filter=1

color_flags=""
if [[ $is_av1 -eq 1 && $needs_filter -eq 0 ]]; then
    echo "STATUS:Video already AV1 with no filtering needed, copying video stream"
    video_params="-c:v copy"
else
    if [[ $is_av1 -eq 1 ]]; then
        echo "STATUS:Video is AV1 but filtering required (crop/scale), re-encoding"
    fi

    # Append HDR params to SVT-AV1 params if HDR is detected
    if [[ $is_hdr -eq 1 ]]; then
        hdr_svt="color-primaries=9:transfer-characteristics=${tc_value}:matrix-coefficients=9"

        # Add mastering display if available (HDR10, not HLG)
        if [[ -n "$mastering_display" ]]; then
            hdr_svt="${hdr_svt}:mastering-display=${mastering_display}"
        fi

        # Add content light level if available
        if [[ -n "$content_light" ]]; then
            hdr_svt="${hdr_svt}:content-light=${content_light}"
        fi

        # Append to user SVT params
        if [[ -n "$SVT_PARAMS" ]]; then
            SVT_PARAMS="${SVT_PARAMS}:${hdr_svt}"
        else
            SVT_PARAMS="$hdr_svt"
        fi

        # ffmpeg-level color metadata flags
        color_flags="-color_primaries bt2020 -color_trc ${color_trc_name} -colorspace bt2020nc -color_range tv"
        echo "STATUS:HDR encoding params applied ($hdr_type)"
    fi

    # luminance-qp-bias reduces dark-scene blockiness; excluded for PQ/HDR10
    # since PQ's luma scale isn't comparable to SDR/HLG's.
    if [[ "$color_transfer" != "smpte2084" && "$SVT_PARAMS" != *"luminance-qp-bias="* ]]; then
        luma_svt="luminance-qp-bias=10"
        if [[ -n "$SVT_PARAMS" ]]; then
            SVT_PARAMS="${SVT_PARAMS}:${luma_svt}"
        else
            SVT_PARAMS="$luma_svt"
        fi
    fi

    svt_params_arg=""
    if [[ -n "$SVT_PARAMS" ]]; then
        svt_params_arg="-svtav1-params $SVT_PARAMS"
    fi
    video_params="-c:v libsvtav1 -preset $PRESET -crf $CRF -g 225 $svt_params_arg"
fi

# --- AUDIO MEASUREMENT + ENCODE (branch A) ---
# Two-pass loudnorm needs a measurement read before the encode read; runs
# concurrently with branch V, which takes far longer at preset 4.
TARGET_I="-20"
TARGET_TP="-2"
TARGET_LRA="13"

# fd 3 is a handle to the real stdout, so branch A can still report STATUS:
# lines while its own stdout (raw ffmpeg output) goes to $audio_log instead.
exec 3>&1

measure_and_encode_audio() {
    local af_filter="" filter_idx=0 idx

    for idx in "${audio_indices[@]}"; do
        LOUDNORM_JSON=$(mktemp)

        nice -n 10 ffmpeg -hide_banner -i "$INPUT_FILE" -map 0:$idx \
            -af "aformat=channel_layouts=stereo,loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:linear=true:print_format=json" \
            -vn -sn -dn -f null - 2> "$LOUDNORM_JSON" > /dev/null

        MEASURED_I=$(sed -n 's/.*"input_i"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null | head -1)
        MEASURED_TP=$(sed -n 's/.*"input_tp"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null | head -1)
        MEASURED_LRA=$(sed -n 's/.*"input_lra"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null | head -1)
        MEASURED_THRESH=$(sed -n 's/.*"input_thresh"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null | head -1)
        TARGET_OFFSET=$(sed -n 's/.*"target_offset"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null | head -1)

        rm -f "$LOUDNORM_JSON"

        if [[ -z "$MEASURED_I" || -z "$MEASURED_TP" || -z "$MEASURED_LRA" || -z "$MEASURED_THRESH" || -z "$TARGET_OFFSET" ]]; then
            echo "STATUS:Failed to parse loudnorm measurements for stream $idx" >&3
            return 1
        fi

        echo "STATUS:Audio stream $idx measurements - I:${MEASURED_I} LUFS, TP:${MEASURED_TP} dBTP, LRA:${MEASURED_LRA} LU" >&3

        af_filter="${af_filter} -filter:a:${filter_idx} aformat=channel_layouts=stereo,loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:linear=true:measured_I=${MEASURED_I}:measured_TP=${MEASURED_TP}:measured_LRA=${MEASURED_LRA}:measured_thresh=${MEASURED_THRESH}:offset=${TARGET_OFFSET}"
        filter_idx=$((filter_idx + 1))
    done

    echo "STATUS:Audio: two-pass normalization on ${#audio_indices[@]} track(s) (target: ${TARGET_I} LUFS, ${TARGET_TP} dBTP, ${TARGET_LRA} LU)" >&3

    local ffmpeg_cmd_a="ffmpeg -i \"$INPUT_FILE\" $audio_map -map_chapters -1 -vn -sn -dn $af_filter -c:a libopus -b:a $AUDIO_BITRATE -f matroska -y \"$tmp_audio\""
    echo "$ffmpeg_cmd_a" > "$AUDIO_CMD_FILE"

    # Full command goes to $AUDIO_CMD_FILE (used for the ENCODER_SETTINGS tag
    # later); not echoed live here since a long one could exceed PIPE_BUF and
    # interleave with branch V's writes on the shared fd 3 pipe.
    echo "STATUS:CMD (audio): audio encode starting (${#audio_indices[@]} track(s))" >&3

    # -map_chapters -1: ffmpeg copies chapters by default even with an
    # explicit stream -map. Branch V already carries them; without this,
    # mkvmerge would merge both branches' copies and double every chapter.
    local audio_start
    audio_start=$(date +%s)

    nice -n 10 ffmpeg -v error -i "$INPUT_FILE" $audio_map -map_chapters -1 -vn -sn -dn \
        $af_filter \
        -c:a libopus -b:a "$AUDIO_BITRATE" \
        -f matroska -y "$tmp_audio"
    local rc=$?

    if [[ $rc -eq 0 ]]; then
        local audio_elapsed=$(( $(date +%s) - audio_start ))
        local audio_size
        audio_size=$(du -h "$tmp_audio" 2>/dev/null | cut -f1)
        echo "STATUS:Audio encode complete: ${#audio_indices[@]} track(s), ${AUDIO_BITRATE}, ${audio_elapsed}s, ${audio_size:-unknown size}" >&3
    fi

    return $rc
}

# --- ENCODING (branch V + branch A, concurrent) ---

echo "STAGE:encoding"
echo "STATUS:Encoding video and audio concurrently..."

# Build the video-branch ffmpeg command (stored in MKV metadata for reproducibility)
FFMPEG_CMD_V="ffmpeg -i \"$INPUT_FILE\" -map 0:v:0 $sub_map -an -dn $vf $video_params $color_flags $sub_codec -f matroska -y \"$tmp_video\""
echo "CMD:$FFMPEG_CMD_V"

# Branch V: video (+ subtitles). The only process permitted to write to the
# wrapper's own stdout - its -progress output drives the UI's progress bar.
nice -n 10 ffmpeg -v quiet -progress - -nostats \
    -i "$INPUT_FILE" \
    -map 0:v:0 $sub_map -an -dn \
    $vf \
    $video_params \
    $color_flags \
    $sub_codec \
    -f matroska -y "$tmp_video" 2>&1 &
pid_v=$!

# Branch A: audio measurement + encode, fully redirected to a log file so it
# can't leak a stray line into branch V's progress output.
{ measure_and_encode_audio; } > "$audio_log" 2>&1 &
pid_a=$!

# Fail fast: stop the other branch as soon as either one fails, rather than
# waiting out its full encode first. wait -n needs bash >= 5.1.
wait -n $pid_v $pid_a
first_rc=$?
if [[ $first_rc -ne 0 ]]; then
    trap '' SIGTERM SIGINT
    kill_all
fi

wait $pid_v
rc_v=$?
wait $pid_a
rc_a=$?

if [[ $rc_v -ne 0 || $rc_a -ne 0 ]]; then
    [[ $rc_v -ne 0 ]] && echo "ERROR:Video encoding failed"
    if [[ $rc_a -ne 0 ]]; then
        echo "ERROR:Audio encoding failed"
        if [[ -f "$audio_log" ]]; then
            while IFS= read -r line; do echo "STATUS:$line"; done < "$audio_log"
        fi
    fi
    # Disarm first: kill_all's pkill -g $$ also signals this script's own
    # PID, which would otherwise re-enter cleanup() and print a spurious
    # "Stopping conversion..." after the real error above.
    trap '' SIGTERM SIGINT
    kill_all
    rm -f "$tmp_video" "$tmp_audio" "$audio_log" "$AUDIO_CMD_FILE"
    exit 1
fi

FFMPEG_CMD_A=$(cat "$AUDIO_CMD_FILE" 2>/dev/null)
rm -f "$AUDIO_CMD_FILE" "$audio_log"

# --- FINALIZATION ---

echo "STAGE:finalizing"
echo "STATUS:Finalizing output file with correct metadata..."

# A/V sync: neither branch seeks, so ffmpeg preserves each stream's original
# container-relative start time - mkvmerge joins them correctly without
# manual realignment.

# Global tags XML: embeds both branches' ffmpeg commands for reproducibility.
TAGS_XML=$(mktemp)
FFMPEG_CMD_XML_V=$(printf '%s' "$FFMPEG_CMD_V" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
FFMPEG_CMD_XML_A=$(printf '%s' "$FFMPEG_CMD_A" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
printf '<?xml version="1.0" encoding="UTF-8"?>\n<Tags>\n  <Tag>\n    <Simple>\n      <Name>ENCODER_SETTINGS</Name>\n      <String>%s\n%s</String>\n    </Simple>\n  </Tag>\n</Tags>\n' "$FFMPEG_CMD_XML_V" "$FFMPEG_CMD_XML_A" > "$TAGS_XML"

# Use mkvmerge to join the two branches, calculate BPS tags, and embed encoding metadata
mkvmerge -o "$OUTPUT_FILE" --global-tags "$TAGS_XML" "$tmp_video" "$tmp_audio" >/dev/null 2>&1
mkvmerge_status=$?
rm -f "$TAGS_XML" "$tmp_video" "$tmp_audio"

if [[ $mkvmerge_status -eq 0 && -f "$OUTPUT_FILE" ]]; then
    echo "STAGE:complete"
    echo "STATUS:Conversion complete"
    exit 0
else
    echo "ERROR:Failed to finalize output file"
    exit 1
fi
