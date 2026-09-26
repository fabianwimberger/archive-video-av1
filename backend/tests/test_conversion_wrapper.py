"""Tests for conversion wrapper configuration."""

import re
import subprocess
import pytest
from pathlib import Path

from app.services.lifecycle import ANIMATED_SVT_PARAMS, BASE_SVT_PARAMS, BUILTIN_PRESETS


WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "conversion_wrapper.sh"
BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build.sh"


@pytest.mark.parametrize("script_path", [WRAPPER, BUILD_SCRIPT])
def test_language_preference_order(script_path):
    script = script_path.read_text()
    start = script.index("find_preferred_stream() {")
    end = script.index("\n}\n", start) + 3
    command = (
        script[start:end] + '\nfind_preferred_stream "1,eng\n2,ger\n3,ger" "ger,eng"\n'
    )
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "2"


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
    # Mirrors the runtime video/audio split; both seek past black/logo intros.
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


def _extract_crop_detect_block():
    script = WRAPPER.read_text()
    start = script.index("        orig_width=$(probe_field")
    end = script.index('echo "STATUS:Consensus:')
    return script[start:end]


def test_crop_detect_filters_asymmetric_samples_before_consensus():
    """Dark scenes yield one-sided crops; real letterboxes are symmetric."""
    block = _extract_crop_detect_block()

    assert "dx = x - (ow - w - x)" in block
    assert "dy = y - (oh - h - y)" in block
    assert "(dx <= 8 && dy <= 8)" in block
    assert '$symmetric" == "yes"' in block
    assert "rejected, asymmetric - likely a dark scene" in block

    # orig_width/orig_height must be resolved once, before sampling starts,
    # so every sample can be checked for symmetry as it comes in.
    assert block.index("orig_width=$(probe_field") < block.index("for percent in")


def test_crop_detect_consensus_threshold_lowered_after_symmetry_filter():
    """Two matching symmetric samples suffice once outliers are filtered."""
    block = _extract_crop_detect_block()

    assert "if ($1 >= 2) print $2" in block
    assert "if ($1 >= 3) print $2" not in block

    # The threshold relies on the symmetry filter to have already run.
    assert block.index('$symmetric" == "yes"') < block.index("if ($1 >= 2)")


def test_crop_detect_symmetry_check_runs_synthetic_samples_correctly():
    """Real samples where 6 of 8 cropdetect windows were dark-scene misses."""
    script = WRAPPER.read_text()
    match = re.search(
        r"symmetric=\$\(echo \"\$crop_value\" \| awk -F'\[=:\]' -v ow=\"\$orig_width\" -v oh=\"\$orig_height\" '(\{.*?\})'\)",
        script,
        re.DOTALL,
    )
    assert match, "symmetry-check awk block not found in conversion_wrapper.sh"
    awk_program = match.group(1)

    samples = {
        "crop=1920:816:0:132": "yes",
        "crop=1764:812:124:136": "no",
        "crop=1912:816:8:132": "yes",
        "crop=1784:816:136:132": "no",
        "crop=992:708:872:238": "no",
        "crop=1812:708:46:136": "no",
        "crop=1880:816:0:132": "no",
    }
    for crop_value, expected in samples.items():
        result = subprocess.run(
            ["awk", "-F", "[=:]", "-v", "ow=1920", "-v", "oh=1080", awk_program],
            input=crop_value,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == expected, crop_value


def _run_match_source_permissions(tmp_path, source_mode, env=None, path=None):
    script = WRAPPER.read_text()
    start = script.index("match_source_permissions() {")
    end = script.index("\n}\n", start) + 3
    source = tmp_path / "source.mkv"
    target = tmp_path / ".source_conv.mkv.XXXX.tmp"
    source.write_bytes(b"source")
    target.write_bytes(b"output")
    source.chmod(source_mode)
    target.chmod(0o600)
    result = subprocess.run(
        [
            "bash",
            "-c",
            script[start:end] + 'match_source_permissions "$1"',
            "-",
            target,
        ],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{path}:/usr/bin:/bin" if path else "/usr/bin:/bin",
            "INPUT_FILE": str(source),
            "OUTPUT_FILE_MODE": "0644",
            **(env or {}),
        },
    )
    return result, source, target


@pytest.mark.parametrize("mode", [0o644, 0o664, 0o640])
def test_output_mirrors_source_mode_and_owner(tmp_path, mode):
    result, source, target = _run_match_source_permissions(tmp_path, mode)
    assert result.returncode == 0, result.stdout
    assert target.stat().st_mode & 0o7777 == mode
    assert (target.stat().st_uid, target.stat().st_gid) == (
        source.stat().st_uid,
        source.stat().st_gid,
    )
    assert "STATUS:" not in result.stdout


def _stub(bin_dir, name, body):
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(f"#!/bin/bash\n{body}\n")
    stub.chmod(0o755)


def test_output_owner_falls_back_to_puid_pgid(tmp_path):
    # A squashing mount refuses chown to the source owner but may still allow
    # the configured one.
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "calls"
    _stub(
        bin_dir,
        "chown",
        f'echo "$*" >> "{calls}"\n[[ "$1" == --reference=* ]] && exit 1\nexit 0',
    )
    result, _source, _target = _run_match_source_permissions(
        tmp_path, 0o644, env={"PUID": "1000", "PGID": "1001"}, path=bin_dir
    )
    assert result.returncode == 0
    assert calls.read_text().splitlines()[-1].startswith("1000:1001 -- ")
    assert "STATUS:" not in result.stdout


def test_output_permission_failures_do_not_fail_the_job(tmp_path):
    # SMB mounts with fixed uid=/gid=/file_mode= refuse both calls.
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "chown", "exit 1")
    _stub(bin_dir, "chmod", "exit 1")
    result, _source, target = _run_match_source_permissions(
        tmp_path, 0o644, env={"PUID": "1000", "PGID": "1000"}, path=bin_dir
    )
    assert result.returncode == 0
    assert target.stat().st_mode & 0o777 == 0o600
    assert "Could not set output file mode" in result.stdout
    assert "Could not set output file owner" in result.stdout


def test_output_mode_falls_back_to_configured_mode(tmp_path):
    bin_dir = tmp_path / "bin"
    _stub(
        bin_dir,
        "chmod",
        '[[ "$1" == --reference=* ]] && exit 1\nexec /usr/bin/chmod "$@"',
    )
    result, _source, target = _run_match_source_permissions(
        tmp_path, 0o640, env={"OUTPUT_FILE_MODE": "0664"}, path=bin_dir
    )
    assert result.returncode == 0
    assert target.stat().st_mode & 0o777 == 0o664


def test_wrapper_applies_source_permissions_before_publishing():
    script = WRAPPER.read_text()
    assert script.index('match_source_permissions "$pending_output"') < script.index(
        'ln -- "$pending_output" "$OUTPUT_FILE"'
    )
