#!/usr/bin/env python3
"""
Script to detect and fix metadata inconsistencies in video files.
Remuxes files with incorrect metadata using ffmpeg -c copy (no re-encoding).
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any


def get_file_info(file_path: Path) -> Optional[Dict[str, Any]]:
    """Get video file information using ffprobe and verify metadata consistency."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path)
            ],
            capture_output=True,
            text=True,
            check=True
        )

        data = json.loads(result.stdout)
        format_info = data.get("format", {})
        streams = data.get("streams", [])

        duration = float(format_info.get("duration", 0))
        if duration == 0:
            return None

        # Get actual file size
        actual_file_size = file_path.stat().st_size

        # Sum up all stream bitrates from BPS tags
        total_stream_bitrate = 0
        stream_details = []

        for stream in streams:
            codec_type = stream.get("codec_type", "unknown")
            codec_name = stream.get("codec_name", "unknown")
            tags = stream.get("tags", {})
            bps_tag = int(tags.get("BPS", 0))

            stream_details.append({
                "type": codec_type,
                "codec": codec_name,
                "bps": bps_tag
            })

            total_stream_bitrate += bps_tag

        # Calculate expected file size from metadata
        # Formula: (sum of all stream bitrates) * duration / 8 = file size in bytes
        if total_stream_bitrate > 0:
            expected_file_size = int((total_stream_bitrate * duration) / 8)
        else:
            expected_file_size = 0

        # Check if metadata matches actual file size (allow 5% tolerance for container overhead)
        has_issue = False
        issues = []

        if expected_file_size == 0:
            has_issue = True
            issues.append("Missing BPS tags in stream metadata")
        else:
            diff_percent = abs(expected_file_size - actual_file_size) / actual_file_size * 100

            if diff_percent > 5.0:
                has_issue = True
                issues.append(
                    f"Metadata size mismatch: {diff_percent:.1f}% difference\n"
                    f"      Expected from metadata: {format_size(expected_file_size)}\n"
                    f"      Actual file size:       {format_size(actual_file_size)}"
                )

        return {
            "has_issue": has_issue,
            "issues": issues,
            "duration": duration,
            "actual_size": actual_file_size,
            "expected_size": expected_file_size,
            "stream_details": stream_details
        }

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, OSError):
        return None


def format_bitrate(bitrate: int) -> str:
    """Format bitrate for human-readable output."""
    if bitrate >= 1_000_000:
        return f"{bitrate / 1_000_000:.2f}Mbps"
    else:
        return f"{bitrate / 1000:.0f}kbps"


def format_size(size: int) -> str:
    """Format file size for human-readable output."""
    if size >= 1_073_741_824:  # 1 GiB
        return f"{size / 1_073_741_824:.2f} GiB"
    elif size >= 1_048_576:  # 1 MiB
        return f"{size / 1_048_576:.2f} MiB"
    elif size >= 1024:  # 1 KiB
        return f"{size / 1024:.2f} KiB"
    else:
        return f"{size} bytes"


def remux_file(file_path: Path, dry_run: bool = False) -> bool:
    """Remux file to fix metadata."""
    temp_file = file_path.parent / f"{file_path.stem}_remux_temp{file_path.suffix}"

    if dry_run:
        print("    [DRY RUN] Would remux to fix metadata")
        return True

    try:
        print("    Remuxing to fix metadata...")

        # Use mkvmerge for MKV files (properly calculates BPS tags)
        # Use ffmpeg for other formats
        if file_path.suffix.lower() == ".mkv":
            result = subprocess.run(
                [
                    "mkvmerge",
                    "-o", str(temp_file),
                    str(file_path)
                ],
                capture_output=True,
                text=True
            )
        else:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i", str(file_path),
                    "-c", "copy",
                    "-map", "0",
                    "-y",
                    str(temp_file)
                ],
                capture_output=True,
                text=True
            )

        if result.returncode != 0:
            error_msg = result.stderr if result.stderr else result.stdout
            print(f"    ✗ Remux failed: {error_msg[:200]}")
            if temp_file.exists():
                temp_file.unlink()
            return False

        # Replace original with remuxed file
        temp_file.replace(file_path)
        print("    ✓ Fixed")
        return True

    except Exception as e:
        print(f"    ✗ Error: {e}")
        if temp_file.exists():
            temp_file.unlink()
        return False


def scan_and_fix(
    root_dir: Path,
    extensions: tuple = (".mkv", ".mp4", ".avi", ".mov", ".webm"),
    dry_run: bool = False
):
    """Scan directory recursively and fix files with metadata issues."""

    print(f"Scanning: {root_dir}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE - will modify files'}")
    print("=" * 80)

    video_files = []
    for ext in extensions:
        video_files.extend(root_dir.rglob(f"*{ext}"))

    if not video_files:
        print("No video files found.")
        return

    print(f"Found {len(video_files)} video files.\n")

    files_checked = 0
    files_with_issues = 0
    files_fixed = 0
    files_failed = 0

    for file_path in sorted(video_files):
        files_checked += 1

        # Show relative path for readability
        try:
            display_path = file_path.relative_to(root_dir)
        except ValueError:
            display_path = file_path

        info = get_file_info(file_path)
        if info is None:
            print(f"[{files_checked}/{len(video_files)}] {display_path}")
            print("  ⚠ Could not read file info")
            continue

        if info["has_issue"]:
            files_with_issues += 1
            print(f"\n[{files_checked}/{len(video_files)}] {display_path}")
            print("  ⚠ Metadata issues detected:")
            for issue in info["issues"]:
                print(f"    - {issue}")

            if remux_file(file_path, dry_run):
                files_fixed += 1
            else:
                files_failed += 1

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Files checked:       {files_checked}")
    print(f"Issues found:        {files_with_issues}")
    if not dry_run:
        print(f"Successfully fixed:  {files_fixed}")
        print(f"Failed to fix:       {files_failed}")
    else:
        print(f"Would fix:           {files_with_issues}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect and fix metadata inconsistencies in video files"
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Root directory to scan (scans all subdirectories)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only detect issues without fixing them"
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".mkv", ".mp4", ".avi", ".mov", ".webm"],
        help="Video file extensions to check (default: .mkv .mp4 .avi .mov .webm)"
    )

    args = parser.parse_args()

    if not args.directory.exists():
        print(f"Error: Directory does not exist: {args.directory}")
        sys.exit(1)

    if not args.directory.is_dir():
        print(f"Error: Not a directory: {args.directory}")
        sys.exit(1)

    try:
        scan_and_fix(
            args.directory,
            tuple(args.extensions),
            args.dry_run
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(1)
