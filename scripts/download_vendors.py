#!/usr/bin/env python3
"""
Download latest vendor libraries for offline use.
Downloads Bootstrap 5 and Bootstrap Icons.
"""

import re
import urllib.request
from pathlib import Path

# Define paths relative to /app (container) or project root
BASE_DIR = Path.cwd()
if (BASE_DIR / "frontend").exists():
    VENDOR_DIR = BASE_DIR / "frontend" / "vendor"
else:
    # Fallback for when running inside container where WORKDIR is /app
    VENDOR_DIR = Path("/app/frontend/vendor")

OPENER = urllib.request.build_opener()
OPENER.addheaders = [("User-Agent", "download_vendors.py")]
urllib.request.install_opener(OPENER)


def download_file(url: str, dest: Path):
    """Download a file from URL to destination"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        print(f"✗ Failed to download {url}: {e}")
        raise


def download_bootstrap():
    """Download Bootstrap 5"""
    base_css = "https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css"
    base_js = "https://cdn.jsdelivr.net/npm/bootstrap@5/dist/js/bootstrap.bundle.min.js"

    # Maps
    map_css = "https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css.map"
    map_js = (
        "https://cdn.jsdelivr.net/npm/bootstrap@5/dist/js/bootstrap.bundle.min.js.map"
    )

    download_file(base_css, VENDOR_DIR / "css" / "bootstrap.min.css")
    download_file(map_css, VENDOR_DIR / "css" / "bootstrap.min.css.map")

    download_file(base_js, VENDOR_DIR / "js" / "bootstrap.bundle.min.js")
    download_file(map_js, VENDOR_DIR / "js" / "bootstrap.bundle.min.js.map")


def download_bootstrap_icons():
    """Download Bootstrap Icons and fonts"""

    # CSS
    css_url = (
        "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css"
    )
    css_dest = VENDOR_DIR / "css" / "bootstrap-icons.css"
    download_file(css_url, css_dest)

    # Parse CSS to find font files
    try:
        with open(css_dest, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex to find font files in the CSS
        # Handles: url("..."), url('...'), url(...)
        # We look for relative paths starting with ./fonts/ or just fonts/
        matches = re.findall(r'url\s*\((?:["\']?)([^"\'\)]+)(?:["\']?)\)', content)

        base_font_url = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1/font/"

        downloaded_fonts = set()

        for relative_url in matches:
            # Clean up URL (remove query params like ?52484601)
            clean_url = relative_url.split("?")[0].split("#")[0]

            # We only care about font files
            if not clean_url.endswith((".woff", ".woff2", ".ttf")):
                continue

            filename = Path(clean_url).name
            if filename in downloaded_fonts:
                continue

            # Download to vendor/css/fonts/
            font_dest = VENDOR_DIR / "css" / "fonts" / filename

            # Construct CDN URL
            # The CSS refers to "./fonts/file", so we map that to the CDN structure
            cdn_url = f"{base_font_url}fonts/{filename}"

            try:
                download_file(cdn_url, font_dest)
                downloaded_fonts.add(filename)
            except Exception as e:
                print(f"Warning: Could not download font {filename}: {e}")

    except Exception as e:
        print(f"Error parsing bootstrap-icons.css: {e}")
        raise


def main():
    try:
        download_bootstrap()
        download_bootstrap_icons()
    except Exception as e:
        print(f"✗ Error downloading libraries: {e}")
        exit(1)


if __name__ == "__main__":
    main()
