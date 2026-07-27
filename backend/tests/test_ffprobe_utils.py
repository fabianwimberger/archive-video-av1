"""Tests for the ffprobe metadata wrapper utilities."""

import json

import pytest

from app.utils.ffprobe import get_video_info, has_converted_file, parse_fps


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


def make_probe_payload(**overrides):
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "30000/1001",
        "color_transfer": "",
        "color_primaries": "",
        "side_data_list": [],
    }
    stream.update(overrides.pop("stream", {}))
    fmt = {"duration": "120.5", "size": "1000000", "bit_rate": "5000000"}
    fmt.update(overrides.pop("format", {}))
    extra_streams = overrides.pop("extra_streams", [])
    return {"streams": [stream, *extra_streams], "format": fmt}


class TestParseFps:
    def test_fraction(self):
        assert parse_fps("30000/1001") == pytest.approx(29.9700, rel=1e-4)

    def test_plain_integer(self):
        assert parse_fps("25") == 25.0

    def test_non_numeric_returns_zero(self):
        assert parse_fps("not-a-number") == 0.0

    def test_zero_denominator_returns_zero(self):
        assert parse_fps("30/0") == 0.0

    def test_malformed_fraction_returns_zero(self):
        assert parse_fps("30/1/1") == 0.0


@pytest.mark.asyncio
class TestGetVideoInfo:
    async def test_returns_none_when_ffprobe_fails(self, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stderr=b"no such file", returncode=1)

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        assert await get_video_info("/videos/missing.mkv") is None

    async def test_returns_none_when_no_video_stream(self, monkeypatch):
        payload = {"streams": [{"codec_type": "audio"}], "format": {}}

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        assert await get_video_info("/videos/audio_only.mkv") is None

    async def test_returns_none_on_unexpected_error(self, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=b"not valid json")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        assert await get_video_info("/videos/broken.mkv") is None

    async def test_parses_standard_dynamic_range_video(self, monkeypatch):
        payload = make_probe_payload()

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        info = await get_video_info("/videos/sdr.mkv")

        assert info["codec"] == "h264"
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["duration"] == 120.5
        assert info["size"] == 1000000
        assert info["bitrate"] == 5000000
        assert info["fps"] == pytest.approx(29.97, rel=1e-3)
        assert info["hdr"] is False
        assert info["hdr_format"] == ""

    async def test_detects_hdr10(self, monkeypatch):
        payload = make_probe_payload(stream={"color_transfer": "smpte2084"})

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        info = await get_video_info("/videos/hdr10.mkv")

        assert info["hdr"] is True
        assert info["hdr_format"] == "HDR10"

    async def test_detects_hlg(self, monkeypatch):
        payload = make_probe_payload(stream={"color_transfer": "arib-std-b67"})

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        info = await get_video_info("/videos/hlg.mkv")

        assert info["hdr"] is True
        assert info["hdr_format"] == "HLG"

    async def test_detects_dolby_vision_side_data(self, monkeypatch):
        payload = make_probe_payload(
            stream={
                "side_data_list": [{"side_data_type": "DOVI configuration record"}],
            }
        )

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        info = await get_video_info("/videos/dv.mkv")

        assert info["hdr"] is True
        assert info["hdr_format"] == "DV"

    async def test_picks_first_video_stream_among_multiple(self, monkeypatch):
        payload = make_probe_payload(extra_streams=[{"codec_type": "subtitle"}])

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        info = await get_video_info("/videos/multi_stream.mkv")

        assert info["codec"] == "h264"


@pytest.mark.asyncio
class TestHasConvertedFile:
    async def test_missing_converted_file(self, tmp_path):
        source = tmp_path / "movie.mkv"
        source.write_bytes(b"source")

        exists, path = await has_converted_file(str(source))

        assert exists is False
        assert path is None

    async def test_empty_converted_file_is_treated_as_missing(self, tmp_path):
        source = tmp_path / "movie.mkv"
        source.write_bytes(b"source")
        (tmp_path / "movie_conv.mkv").touch()

        exists, path = await has_converted_file(str(source))

        assert exists is False
        assert path is None

    async def test_existing_converted_file_is_reported(self, tmp_path):
        source = tmp_path / "movie.mkv"
        source.write_bytes(b"source")
        converted = tmp_path / "movie_conv.mkv"
        converted.write_bytes(b"converted-bytes")

        exists, path = await has_converted_file(str(source))

        assert exists is True
        assert path == str(converted)
