"""Validation utilities for conversion settings and presets."""

import re
from typing import Dict, Any

# Allowed characters for SVT-AV1 parameters (key=value pairs separated by colons)
SVT_PARAMS_PATTERN = re.compile(r"^[a-zA-Z0-9_\-=/:.]*$")

# Audio bitrate pattern: number followed by k (e.g., 96k, 128k, 320k)
AUDIO_BITRATE_PATTERN = re.compile(r"^\d+[kK]$")

PRESET_NAME_PATTERN = re.compile(r"^[\w\- ()]{1,64}$")


def validate_conversion_settings(settings: Dict[str, Any]) -> None:
    """Validate conversion settings to prevent command injection."""
    # Values reach the encoder command line, so only plain key=value pairs pass.
    svt_params = settings.get("svt_params", "")
    if not SVT_PARAMS_PATTERN.match(svt_params):
        raise ValueError(
            "Invalid SVT parameters format. Only alphanumeric characters, "
            "underscores, hyphens, colons, equals signs, dots, and forward slashes are allowed."
        )

    audio_bitrate = settings.get("audio_bitrate", "")
    if not AUDIO_BITRATE_PATTERN.match(audio_bitrate):
        raise ValueError(
            "Invalid audio bitrate format. Expected format: number followed by 'k' or 'K' (e.g., 96k, 128k)."
        )

    crf = settings.get("crf")
    if not isinstance(crf, int) or not (0 <= crf <= 51):
        raise ValueError("CRF must be an integer between 0 and 51.")

    preset = settings.get("encoder_preset")
    if not isinstance(preset, int) or not (0 <= preset <= 13):
        raise ValueError("encoder_preset must be an integer between 0 and 13.")

    max_resolution = settings.get("max_resolution", 1080)
    if max_resolution not in {720, 1080, 2160}:
        raise ValueError("max_resolution must be one of 720, 1080, or 2160.")


def validate_preset_name(name: str) -> None:
    """Validate preset name format."""
    if not name or not PRESET_NAME_PATTERN.match(name):
        raise ValueError(
            "Preset name must be 1-64 characters and can only contain letters, numbers, spaces, underscores, hyphens, and parentheses."
        )
