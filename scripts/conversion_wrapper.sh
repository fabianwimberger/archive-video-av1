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

TEMP_DIR="${TEMP_DIR:-/app/temp}"

# --- TRAP SIGNALS ---

cleanup() {
    echo "STATUS:Stopping conversion..."
    # Kill all child processes in the current process group
    pkill -P $$

    # Clean up temp files
    if [[ -n "$temp_file" && -f "$temp_file" ]]; then
        rm -f "$temp_file"
    fi
    if [[ -n "$LOUDNORM_JSON" && -f "$LOUDNORM_JSON" ]]; then
        rm -f "$LOUDNORM_JSON"
    fi
    if [[ -n "$TAGS_XML" && -f "$TAGS_XML" ]]; then
        rm -f "$TAGS_XML"
    fi

    exit 1
}
trap cleanup SIGTERM SIGINT

echo "STAGE:initializing"

# --- HELPER FUNCTIONS (from original script) ---

get_total_frames() {
    local video="$1"
    local frames=$(ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of default=noprint_wrappers=1:nokey=1 "$video")

    if [[ ! "$frames" =~ ^[0-9]+$ ]]; then
        local duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$video")
        local fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=noprint_wrappers=1:nokey=1 "$video")

        if [[ -n "$duration" && -n "$fps" && "$duration" != "N/A" ]]; then
            frames=$(awk -v d="$duration" -v f="$fps" 'BEGIN { split(f,a,"/"); rate=a[1]/a[2]; printf "%.0f", d*rate }')
        fi
    fi
    echo "${frames:-0}"
}


# --- MAIN CONVERSION LOGIC ---

# Use same directory as output file for temp file
output_dir="$(dirname "$OUTPUT_FILE")"
temp_file="${output_dir}/.$(basename "$OUTPUT_FILE").tmp"

# Get total frames for progress calculation
TOTAL_FRAMES=$(get_total_frames "$INPUT_FILE")
echo "total_frames=$TOTAL_FRAMES"

# Detect video codec
video_codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$INPUT_FILE")
is_av1=0
[[ "$video_codec" == "av1" ]] && is_av1=1
echo "STATUS:Detected video codec: $video_codec"

# Detect crop
crop=""
if [[ $is_av1 -eq 0 && $SKIP_CROP -eq 0 ]]; then
    echo "STAGE:crop_detect"
    echo "STATUS:Detecting crop parameters..."

    # Get video duration for percentage-based sampling
    duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$INPUT_FILE")

    if [[ -n "$duration" && "$duration" != "N/A" ]]; then
        # Collect all crop values by sampling at 8 points
        all_crops=""
        for percent in 10 20 30 40 50 60 70 80; do
            time=$(awk -v d="$duration" -v p="$percent" 'BEGIN { printf "%.0f", d * p / 100 }')

            # Run cropdetect - filter analysis to null output
            crop_value=$(ffmpeg -hide_banner -ss $time -i "$INPUT_FILE" -t 3 -vf cropdetect -an -f null - 2>&1 | grep -o 'crop=[0-9:]*' | tail -1)

            echo "STATUS:Sample ${percent}% (@${time}s): ${crop_value:-none}"

            # Only add non-empty values
            [[ -n "$crop_value" ]] && all_crops="${all_crops}${crop_value}"$'\n'
        done

        # Find consensus: require exactly 3 or more exact matches across all parameters
        consensus=$(echo "$all_crops" | grep -v '^$' | sort | uniq -c | sort -rn | head -1)
        consensus_count=$(echo "$consensus" | awk '{print $1}')
        crop=$(echo "$consensus" | awk '{if ($1 >= 3) print $2}')

        echo "STATUS:Consensus: ${consensus_count:-0} matches for $(echo "$consensus" | awk '{print $2}')"

        if [[ -n "$crop" ]]; then
            # Get original resolution
            orig_res=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$INPUT_FILE")
            orig_width=$(echo "$orig_res" | cut -d',' -f1)
            orig_height=$(echo "$orig_res" | cut -d',' -f2)
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
vf=""
[[ -n "$crop" ]] && vf="-vf $crop,format=yuv420p10le" || vf="-vf format=yuv420p10le"

# Detect audio/subs
audio_streams=$(ffprobe -v error -select_streams a -show_entries stream=index:stream_tags=language -of csv=p=0 "$INPUT_FILE")
german_audio=$(echo "$audio_streams" | grep -m1 ",ger\|,deu\|,de" | cut -d',' -f1)
english_audio=$(echo "$audio_streams" | grep -m1 ",eng\|,en" | cut -d',' -f1)
first_audio=$(echo "$audio_streams" | head -1 | cut -d',' -f1)
audio_idx="${german_audio:-${english_audio:-$first_audio}}"

if [[ -z "$audio_idx" ]]; then
    echo "ERROR:No audio streams found"
    exit 1
fi

# --- AUDIO FILTER CHAIN ---
# Two-pass loudnorm with safe downmix
# Pass 1: Measure audio characteristics
# Pass 2: Apply normalization with measured values (eliminates pumping)
TARGET_I="-20"
TARGET_TP="-2"
TARGET_LRA="13"

echo "STAGE:audio_measure"
echo "STATUS:Measuring audio for two-pass normalization..."

# Create temp file for measurement JSON
LOUDNORM_JSON=$(mktemp)

# Run pass 1: measurement
ffmpeg -hide_banner -i "$INPUT_FILE" -map 0:$audio_idx \
    -af "aformat=channel_layouts=stereo,loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:linear=true:print_format=json" \
    -vn -sn -dn -f null - 2> "$LOUDNORM_JSON" > /dev/null

# Parse JSON output (using sed for BusyBox compatibility)
MEASURED_I=$(sed -n 's/.*"input_i"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null)
MEASURED_TP=$(sed -n 's/.*"input_tp"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null)
MEASURED_LRA=$(sed -n 's/.*"input_lra"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null)
MEASURED_THRESH=$(sed -n 's/.*"input_thresh"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null)
TARGET_OFFSET=$(sed -n 's/.*"target_offset"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LOUDNORM_JSON" 2>/dev/null)

rm -f "$LOUDNORM_JSON"

# Validate we got measurements
if [[ -z "$MEASURED_I" || -z "$MEASURED_TP" || -z "$MEASURED_LRA" || -z "$MEASURED_THRESH" || -z "$TARGET_OFFSET" ]]; then
    echo "ERROR:Failed to parse loudnorm measurements"
    exit 1
fi

echo "STATUS:Audio measurements - I:${MEASURED_I} LUFS, TP:${MEASURED_TP} dBTP, LRA:${MEASURED_LRA} LU"

# Build pass 2 filter with measured values
af_filter="-af aformat=channel_layouts=stereo,loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=${TARGET_LRA}:linear=true:measured_I=${MEASURED_I}:measured_TP=${MEASURED_TP}:measured_LRA=${MEASURED_LRA}:measured_thresh=${MEASURED_THRESH}:offset=${TARGET_OFFSET}"
echo "STATUS:Audio: two-pass normalization (target: ${TARGET_I} LUFS, ${TARGET_TP} dBTP, ${TARGET_LRA} LU)"

subtitle_info=$(ffprobe -v error -select_streams s -show_entries stream=index:stream_tags=language -of csv=p=0 "$INPUT_FILE")
sub_map=""
if [[ -n "$german_audio" ]]; then
    german_sub=$(echo "$subtitle_info" | grep -m1 ",ger\|,deu\|,de" | cut -d',' -f1)
    [[ -n "$german_sub" ]] && sub_map="-map 0:$german_sub"
elif [[ -n "$english_audio" ]]; then
    english_sub=$(echo "$subtitle_info" | grep -m1 ",eng\|,en" | cut -d',' -f1)
    [[ -n "$english_sub" ]] && sub_map="-map 0:$english_sub"
fi

# Determine video encoding parameters
# Only copy if input is AV1 AND no crop needed
if [[ $is_av1 -eq 1 && -z "$crop" ]]; then
    echo "STATUS:Video already AV1 with no filtering needed, copying video stream"
    video_params="-c:v copy"
else
    if [[ $is_av1 -eq 1 ]]; then
        echo "STATUS:Video is AV1 but filtering required (crop), re-encoding"
    fi
    svt_params_arg=""
    if [[ -n "$SVT_PARAMS" ]]; then
        svt_params_arg="-svtav1-params $SVT_PARAMS"
    fi
    video_params="-c:v libsvtav1 -preset $PRESET -crf $CRF -g 225 $svt_params_arg"
fi

# --- ENCODING ---

echo "STAGE:encoding"
echo "STATUS:Encoding video..."

# Build the full ffmpeg command (stored in MKV metadata for reproducibility)
FFMPEG_CMD="ffmpeg -i \"$INPUT_FILE\" -map 0:v:0 -map 0:$audio_idx $sub_map $vf $af_filter $video_params -c:a libopus -b:a $AUDIO_BITRATE -c:s copy -f matroska -y \"$OUTPUT_FILE\""
echo "CMD:$FFMPEG_CMD"

nice -n 10 ffmpeg -v quiet -progress - -nostats \
    -i "$INPUT_FILE" \
    -map 0:v:0 -map 0:$audio_idx $sub_map \
    $vf \
    $af_filter \
    $video_params \
    -c:a libopus -b:a $AUDIO_BITRATE \
    -c:s copy \
    -f matroska -y "$temp_file" 2>&1

ffmpeg_status=$?

if [[ $ffmpeg_status -ne 0 ]]; then
    echo "ERROR:FFmpeg encoding failed"
    rm -f "$temp_file"
    exit 1
fi

# --- FINALIZATION ---

echo "STAGE:finalizing"
echo "STATUS:Finalizing output file with correct metadata..."

# Build MKV global tags XML with the ffmpeg command for reproducibility
TAGS_XML=$(mktemp)
FFMPEG_CMD_XML=$(printf '%s' "$FFMPEG_CMD" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
printf '<?xml version="1.0" encoding="UTF-8"?>\n<Tags>\n  <Tag>\n    <Simple>\n      <Name>ENCODER_SETTINGS</Name>\n      <String>%s</String>\n    </Simple>\n  </Tag>\n</Tags>\n' "$FFMPEG_CMD_XML" > "$TAGS_XML"

# Use mkvmerge to remux, calculate BPS tags, and embed encoding metadata
mkvmerge -o "$OUTPUT_FILE" --global-tags "$TAGS_XML" "$temp_file" >/dev/null 2>&1
mkvmerge_status=$?
rm -f "$TAGS_XML"

if [[ $mkvmerge_status -eq 0 && -f "$OUTPUT_FILE" ]]; then
    echo "STAGE:complete"
    echo "STATUS:Conversion complete"
    rm -f "$temp_file"
    exit 0
else
    echo "ERROR:Failed to finalize output file"
    rm -f "$temp_file"
    exit 1
fi
