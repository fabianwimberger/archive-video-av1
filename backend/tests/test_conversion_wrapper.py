"""Tests for conversion wrapper configuration."""

import re
from pathlib import Path

from app.services.lifecycle import ANIMATED_SVT_PARAMS, BASE_SVT_PARAMS, BUILTIN_PRESETS


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
    # concurrent branch V / branch A split in conversion_wrapper.sh. Both seek
    # into the file first so training doesn't land on a black/logo intro.
    assert (
        'ffmpeg -hide_banner -ss "$train_ss" -i "$f" -map 0:v:0 -an -sn -dn -t 15'
        in script
    )
    assert (
        'ffmpeg -hide_banner -ss "$train_ss" -i "$f" -map 0:$audio_idx -vn -sn -dn -t 15'
        in script
    )


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
    assert match, 'base_svt="..." literal not found in build.sh'
    assert match.group(1) == BASE_SVT_PARAMS

    match = re.search(r'animated_svt="([^"]+)"', script)
    assert match, 'animated_svt="..." literal not found in build.sh'
    assert match.group(1) == ANIMATED_SVT_PARAMS


def test_pgo_training_numbers_match_builtin_presets():
    """Per-prefix CRF/film-grain in build.sh must match BUILTIN_PRESETS."""
    script = BUILD_SCRIPT.read_text()
    presets = {p["name"]: p for p in BUILTIN_PRESETS}

    default_crf = re.search(r"\n\s*preset_crf=(\d+)\n\s*svt_base=", script)
    assert default_crf, "default preset_crf=... literal not found in build.sh"
    assert int(default_crf.group(1)) == presets["Default"]["crf"]

    animated_block = re.search(r"animated_\*\)(.*?);;", script, re.DOTALL)
    assert animated_block, "animated_*) case block not found in build.sh"
    animated_crf = re.search(r"preset_crf=(\d+)", animated_block.group(1))
    assert animated_crf and int(animated_crf.group(1)) == presets["Animated"]["crf"]

    grainy_block = re.search(r"grainy_\*\)(.*?);;", script, re.DOTALL)
    assert grainy_block, "grainy_*) case block not found in build.sh"
    grainy_crf = re.search(r"preset_crf=(\d+)", grainy_block.group(1))
    assert grainy_crf and int(grainy_crf.group(1)) == presets["Grainy"]["crf"]
    grainy_grain = re.search(r"film-grain=(\d+)", grainy_block.group(1))
    assert grainy_grain, "film-grain=... not found in grainy_*) block"
    assert f"film-grain={grainy_grain.group(1)}" in presets["Grainy"]["svt_params"]

    verygrainy_block = re.search(r"verygrainy_\*\)(.*?);;", script, re.DOTALL)
    assert verygrainy_block, "verygrainy_*) case block not found in build.sh"
    verygrainy_crf = re.search(r"preset_crf=(\d+)", verygrainy_block.group(1))
    assert (
        verygrainy_crf and int(verygrainy_crf.group(1)) == presets["Very Grainy"]["crf"]
    )
    verygrainy_grain = re.search(r"film-grain=(\d+)", verygrainy_block.group(1))
    assert verygrainy_grain, "film-grain=... not found in verygrainy_*) block"
    assert (
        f"film-grain={verygrainy_grain.group(1)}"
        in presets["Very Grainy"]["svt_params"]
    )


def test_pgo_training_matches_runtime_encode_constants():
    """-preset/-g/luminance-qp-bias in build.sh must match conversion_wrapper.sh."""
    build_script = BUILD_SCRIPT.read_text()
    wrapper_script = WRAPPER.read_text()

    assert all(p["encoder_preset"] == 4 for p in BUILTIN_PRESETS)
    assert "-preset 4 -crf $preset_crf -g 225" in build_script
    assert "-preset $PRESET -crf $CRF -g 225" in wrapper_script

    assert "luminance-qp-bias=10" in build_script
    assert 'luma_svt="luminance-qp-bias=10"' in wrapper_script
