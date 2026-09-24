#!/usr/bin/env python3
"""Download pinned vendor libraries for offline use.

Bootstrap and Bootstrap Icons are pinned to exact versions and verified
against SHA-256 checksums, so builds are reproducible and a compromised or
changed CDN asset cannot silently end up in the image. Update the version
constants and the hashes together when bumping.
"""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

BOOTSTRAP_VERSION = "5.3.8"
BOOTSTRAP_ICONS_VERSION = "1.13.1"

CDN = "https://cdn.jsdelivr.net/npm"

# dest (relative to the vendor dir) -> (path on the CDN, sha256)
BOOTSTRAP_FILES = {
    "css/bootstrap.min.css": (
        "dist/css/bootstrap.min.css",
        "d85327d99c7a3ee1f9b5d0500d1370acea3ad2db39c163c2f51f232baedbdede",
    ),
    "css/bootstrap.min.css.map": (
        "dist/css/bootstrap.min.css.map",
        "48144faf6aa0fb3cd2ce748d9730238f888f4ab715f05dabd1c9af2c5671988a",
    ),
    "js/bootstrap.bundle.min.js": (
        "dist/js/bootstrap.bundle.min.js",
        "e4fd49181388c48ec5040bd3fe66f57c29c8e67fcd8502b3354b96ec7ab47cc7",
    ),
    "js/bootstrap.bundle.min.js.map": (
        "dist/js/bootstrap.bundle.min.js.map",
        "c61123e58cc0a4b65d737ba070c485911b3dbec6d7b802bdf6628395abd9c08b",
    ),
}

BOOTSTRAP_ICONS_CSS = (
    "css/bootstrap-icons.css",
    "004322721c8557331759bc6ddaacbb689b0f0715d688aec82bd056d2d5b5cc3b",
)

# Referenced by the pinned bootstrap-icons.css above.
BOOTSTRAP_ICONS_FONTS = {
    "bootstrap-icons.woff": "f55513b7b591cb84a3b87ff0e34ea24d4831d6fedc22e54b911ca64b5b544a15",
    "bootstrap-icons.woff2": "6c75710364a1ca5604267716f6d28997b26319fdb078cf11e0b42ab66ff2ea61",
}

BASE_DIR = Path.cwd()
if (BASE_DIR / "frontend").exists():
    VENDOR_DIR = BASE_DIR / "frontend" / "vendor"
else:
    # Fallback for when running inside container where WORKDIR is /app
    VENDOR_DIR = Path("/app/frontend/vendor")

OPENER = urllib.request.build_opener()
OPENER.addheaders = [("User-Agent", "download_vendors.py")]
urllib.request.install_opener(OPENER)


def download_file(url: str, dest: Path, expected_sha256: str) -> None:
    """Download a file from URL to destination and verify its checksum."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        raise

    actual = hashlib.sha256(dest.read_bytes()).hexdigest()
    if actual != expected_sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {url}: expected {expected_sha256}, got {actual}"
        )


def download_bootstrap() -> None:
    for dest_rel, (remote, sha256) in BOOTSTRAP_FILES.items():
        url = f"{CDN}/bootstrap@{BOOTSTRAP_VERSION}/{remote}"
        download_file(url, VENDOR_DIR / dest_rel, sha256)


def download_bootstrap_icons() -> None:
    """Download Bootstrap Icons CSS and the fonts it references."""
    css_rel, css_sha256 = BOOTSTRAP_ICONS_CSS
    css_dest = VENDOR_DIR / css_rel
    download_file(
        f"{CDN}/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/{Path(css_rel).name}",
        css_dest,
        css_sha256,
    )

    content = css_dest.read_text(encoding="utf-8")

    matches = re.findall(r'url\s*\((?:["\']?)([^"\'\)]+)(?:["\']?)\)', content)

    downloaded_fonts = set()
    for relative_url in matches:
        clean_url = relative_url.split("?")[0].split("#")[0]

        if not clean_url.endswith((".woff", ".woff2", ".ttf")):
            continue

        filename = Path(clean_url).name
        if filename in downloaded_fonts:
            continue

        sha256 = BOOTSTRAP_ICONS_FONTS.get(filename)
        if sha256 is None:
            raise RuntimeError(
                f"bootstrap-icons@{BOOTSTRAP_ICONS_VERSION} references unknown font "
                f"{filename}; update BOOTSTRAP_ICONS_FONTS"
            )

        font_dest = VENDOR_DIR / "css" / "fonts" / filename
        url = f"{CDN}/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/fonts/{filename}"
        download_file(url, font_dest, sha256)
        downloaded_fonts.add(filename)


def main() -> None:
    try:
        download_bootstrap()
        download_bootstrap_icons()
    except Exception as e:
        print(f"Error downloading libraries: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
