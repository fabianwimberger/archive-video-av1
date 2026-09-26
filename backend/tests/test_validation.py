"""Tests for validation utilities."""

import pytest
from app.utils.validation import validate_conversion_settings, validate_preset_name


class TestValidateConversionSettings:
    def test_valid_settings(self):
        settings = {
            "crf": 26,
            "encoder_preset": 4,
            "svt_params": "tune=0:film-grain=8",
            "audio_bitrate": "96k",
            "skip_crop_detect": False,
            "max_resolution": 1080,
        }
        validate_conversion_settings(settings)  # Should not raise

    def test_invalid_svt_params(self):
        settings = {
            "crf": 26,
            "encoder_preset": 4,
            "svt_params": "tune=0; rm -rf /",
            "audio_bitrate": "96k",
            "skip_crop_detect": False,
            "max_resolution": 1080,
        }
        with pytest.raises(ValueError, match="Invalid SVT parameters"):
            validate_conversion_settings(settings)

    def test_invalid_audio_bitrate(self):
        settings = {
            "crf": 26,
            "encoder_preset": 4,
            "svt_params": "",
            "audio_bitrate": "invalid",
            "skip_crop_detect": False,
            "max_resolution": 1080,
        }
        with pytest.raises(ValueError, match="Invalid audio bitrate"):
            validate_conversion_settings(settings)

    def test_invalid_crf_range(self):
        settings = {
            "crf": 60,
            "encoder_preset": 4,
            "svt_params": "",
            "audio_bitrate": "96k",
            "skip_crop_detect": False,
            "max_resolution": 1080,
        }
        with pytest.raises(ValueError, match="CRF must be"):
            validate_conversion_settings(settings)

    def test_invalid_preset_range(self):
        settings = {
            "crf": 26,
            "encoder_preset": 20,
            "svt_params": "",
            "audio_bitrate": "96k",
            "skip_crop_detect": False,
            "max_resolution": 1080,
        }
        with pytest.raises(ValueError, match="encoder_preset must be"):
            validate_conversion_settings(settings)

    def test_invalid_max_resolution(self):
        settings = {
            "crf": 26,
            "encoder_preset": 4,
            "svt_params": "",
            "audio_bitrate": "96k",
            "skip_crop_detect": False,
            "max_resolution": 900,
        }
        with pytest.raises(ValueError, match="max_resolution must be"):
            validate_conversion_settings(settings)


class TestValidatePresetName:
    def test_valid_names(self):
        validate_preset_name("Default")
        validate_preset_name("My Preset (4K)")
        validate_preset_name("a-b_c")

    def test_empty_name(self):
        with pytest.raises(ValueError):
            validate_preset_name("")

    def test_invalid_characters(self):
        with pytest.raises(ValueError):
            validate_preset_name("Preset<script>")

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_preset_name("a" * 65)
