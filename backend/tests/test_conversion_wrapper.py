"""Tests for conversion wrapper configuration."""

import re
from pathlib import Path

from app.services.lifecycle import ANIMATED_SVT_PARAMS, BASE_SVT_PARAMS


WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "conversion_wrapper.sh"
BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build.sh"


def test_track_selection_defaults_are_configurable():
    script = WRAPPER.read_text()

    assert 'AUDIO_TRACK_MODE="${AUDIO_TRACK_MODE:-preferred}"' in script
    assert 'SUBTITLE_TRACK_MODE="${SUBTITLE_TRACK_MODE:-preferred}"' in script
    assert (
        'PREFERRED_AUDIO_LANGUAGES="${PREFERRED_AUDIO_LANGUAGES:-ger,deu,de,eng,en}"'
        in script
    )
    assert (
        'PREFERRED_SUBTITLE_LANGUAGES="${PREFERRED_SUBTITLE_LANGUAGES:-ger,deu,de,eng,en}"'
        in script
    )


def test_all_track_modes_are_supported():
    script = WRAPPER.read_text()

    assert 'AUDIO_TRACK_MODE" in' in script
    assert 'audio_map="-map 0:a"' in script
    assert 'SUBTITLE_TRACK_MODE" in' in script
    assert 'sub_map="-map 0:s?"' in script
    assert "none)" in script


def test_pgo_training_uses_preferred_audio_stream():
    script = BUILD_SCRIPT.read_text()

    assert (
        'PREFERRED_AUDIO_LANGUAGES="${PREFERRED_AUDIO_LANGUAGES:-ger,deu,de,eng,en}"'
        in script
    )
    assert (
        'preferred_audio=$(find_preferred_stream "$audio_streams" "$PREFERRED_AUDIO_LANGUAGES")'
        in script
    )
    assert 'ffmpeg -hide_banner -i "$f" -map 0:$audio_idx -t 10' in script
    # Training encodes video and audio as separate invocations, mirroring the
    # concurrent branch V / branch A split in conversion_wrapper.sh.
    assert 'ffmpeg -hide_banner -i "$f" -map 0:v:0 -an -sn -dn -t 15' in script
    assert 'ffmpeg -hide_banner -i "$f" -map 0:$audio_idx -vn -sn -dn -t 15' in script


def test_only_video_branch_emits_progress():
    script = WRAPPER.read_text()

    assert "-progress -" in script

    start = script.index("measure_and_encode_audio()")
    end = script.index("\n}\n", start)
    audio_branch = script[start:end]

    assert "-progress" not in audio_branch


def test_pgo_training_svt_base_matches_builtin_presets():
    """PGO training's base SVT params must match BUILTIN_PRESETS, or the
    profiled encoder settings silently diverge from what real jobs use."""
    script = BUILD_SCRIPT.read_text()

    match = re.search(r'base_svt="([^"]+)"', script)
    assert match, "base_svt=\"...\" literal not found in build.sh"
    assert match.group(1) == BASE_SVT_PARAMS

    match = re.search(r'animated_svt="([^"]+)"', script)
    assert match, "animated_svt=\"...\" literal not found in build.sh"
    assert match.group(1) == ANIMATED_SVT_PARAMS
