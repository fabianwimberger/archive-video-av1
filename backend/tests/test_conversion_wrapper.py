"""Tests for conversion wrapper configuration."""

from pathlib import Path


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
    assert 'ffmpeg -hide_banner -i "$f" -map 0:v:0 -map 0:$audio_idx -t 15' in script
