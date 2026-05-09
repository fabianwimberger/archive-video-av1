"""Tests for conversion wrapper configuration."""

from pathlib import Path


WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "conversion_wrapper.sh"


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
