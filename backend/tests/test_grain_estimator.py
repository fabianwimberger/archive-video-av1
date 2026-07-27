"""Tests for the grain/denoise heuristic estimator."""

import pytest

from app.services.grain_estimator import estimate_grain


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b""):
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def make_fake_exec(
    duration="10.0", resolution="1920,1080", bitrate="5000000", stdev_lines=None
):
    """Build a fake asyncio.create_subprocess_exec dispatching on the probed command."""
    stdev_lines = stdev_lines if stdev_lines is not None else []
    state = {"ffmpeg_call": 0}

    async def fake_exec(*cmd, **kwargs):
        if cmd[0] == "ffprobe":
            if "format=duration" in cmd:
                return FakeProcess(stdout=duration.encode())
            if "stream=width,height" in cmd:
                return FakeProcess(stdout=resolution.encode())
            if "format=bit_rate" in cmd:
                return FakeProcess(stdout=bitrate.encode())
            raise AssertionError(f"unexpected ffprobe command: {cmd}")
        if cmd[0] == "ffmpeg":
            idx = state["ffmpeg_call"]
            state["ffmpeg_call"] += 1
            line = stdev_lines[idx] if idx < len(stdev_lines) else ""
            return FakeProcess(stderr=line.encode())
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_exec


def stdev_line(y, u, v):
    return f"[Parsed_showinfo] n:0 stdev:[{y} {u} {v}]"


@pytest.mark.asyncio
async def test_zero_duration_returns_low_confidence_fallback(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", make_fake_exec(duration="0"))

    result = await estimate_grain("/videos/movie.mkv")

    assert result["film_grain"] == 8
    assert result["denoise"] == 0
    assert result["confidence"] == "low"
    assert "duration" in result["reason"].lower()


@pytest.mark.asyncio
async def test_unparseable_duration_returns_fallback(monkeypatch):
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", make_fake_exec(duration="not-a-number")
    )

    result = await estimate_grain("/videos/movie.mkv")

    assert result["confidence"] == "low"
    assert "duration" in result["reason"].lower()


@pytest.mark.asyncio
async def test_no_stdev_samples_returns_fallback(monkeypatch):
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        make_fake_exec(stdev_lines=["no useful output"] * 4),
    )

    result = await estimate_grain("/videos/movie.mkv")

    assert result["confidence"] == "low"
    assert "analyze" in result["reason"].lower()


@pytest.mark.asyncio
async def test_animation_like_content_disables_grain(monkeypatch):
    # 1080p -> norm_factor 1.5; y_norm = 15/1.5 = 10 (< 20), chroma high on both channels.
    lines = [stdev_line(15.0, 10.0, 10.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", make_fake_exec(stdev_lines=lines)
    )

    result = await estimate_grain("/videos/cartoon.mkv")

    assert result == {
        "film_grain": 0,
        "denoise": 0,
        "confidence": "high",
        "raw_y": 15.0,
        "raw_u": 10.0,
        "raw_v": 10.0,
        "y_norm": 10.0,
        "bitrate_per_mp": pytest.approx(5000000 / 2.0736 / 1000, rel=1e-3),
        "reason": "High chroma, low luma variation (animation-like content)",
    }


@pytest.mark.asyncio
async def test_grainy_film_detection(monkeypatch):
    # y_norm = 45/1.5 = 30, within (20, 60).
    lines = [stdev_line(45.0, 2.0, 2.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", make_fake_exec(stdev_lines=lines)
    )

    result = await estimate_grain("/videos/film.mkv")

    assert result["film_grain"] == 10
    assert result["denoise"] == 1
    assert result["confidence"] == "medium"
    assert result["y_norm"] == 30.0
    assert "grainy film" in result["reason"].lower()


@pytest.mark.asyncio
async def test_high_detail_low_bitrate_boosts_grain(monkeypatch):
    # y_norm = 100/1.5 = 66.7 (>= 60); low bitrate per megapixel.
    lines = [stdev_line(100.0, 2.0, 2.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        make_fake_exec(bitrate="2000000", stdev_lines=lines),
    )

    result = await estimate_grain("/videos/detailed_lowbitrate.mkv")

    assert result["film_grain"] == 16
    assert result["denoise"] == 1
    assert "low bitrate" in result["reason"].lower()


@pytest.mark.asyncio
async def test_high_detail_good_bitrate_moderates_grain(monkeypatch):
    lines = [stdev_line(100.0, 2.0, 2.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        make_fake_exec(bitrate="20000000", stdev_lines=lines),
    )

    result = await estimate_grain("/videos/detailed_highbitrate.mkv")

    assert result["film_grain"] == 12
    assert result["denoise"] == 0
    assert "good bitrate" in result["reason"].lower()


@pytest.mark.asyncio
async def test_default_mapping_low_texture(monkeypatch):
    # y_norm = 10/1.5 = 6.67 (< 15) and chroma low, so falls through to the default mapping.
    lines = [stdev_line(10.0, 1.0, 1.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", make_fake_exec(stdev_lines=lines)
    )

    result = await estimate_grain("/videos/clean.mkv")

    assert result["film_grain"] == 4
    assert result["denoise"] == 0
    assert result["reason"].startswith("Y_norm=")


@pytest.mark.asyncio
async def test_default_mapping_mid_texture(monkeypatch):
    # y_norm = 27/1.5 = 18 (15 <= y_norm < 30).
    lines = [stdev_line(27.0, 1.0, 1.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", make_fake_exec(stdev_lines=lines)
    )

    result = await estimate_grain("/videos/mild_texture.mkv")

    assert result["film_grain"] == 8


@pytest.mark.asyncio
async def test_4k_resolution_uses_higher_normalization_factor(monkeypatch):
    # 4K -> norm_factor 3.0; y_norm = 30/3.0 = 10.
    lines = [stdev_line(30.0, 1.0, 1.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        make_fake_exec(resolution="3840,2160", stdev_lines=lines),
    )

    result = await estimate_grain("/videos/4k.mkv")

    assert result["y_norm"] == 10.0


@pytest.mark.asyncio
async def test_malformed_resolution_and_bitrate_fall_back_to_defaults(monkeypatch):
    lines = [stdev_line(10.0, 1.0, 1.0)] * 4
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        make_fake_exec(
            resolution="not-a-resolution", bitrate="not-a-number", stdev_lines=lines
        ),
    )

    result = await estimate_grain("/videos/weird.mkv")

    # Falls back to the 1920x1080 default -> norm_factor 1.5, and bitrate 0 -> bitrate_per_mp 0.
    assert result["y_norm"] == round(10.0 / 1.5, 2)
    assert result["bitrate_per_mp"] == 0.0
