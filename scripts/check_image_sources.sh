#!/usr/bin/env bash
set -euo pipefail

cd /usr/share/source/archive-video-av1

check_notice() {
    local archive=$1
    local root=${archive%.tar.gz}
    tar -xOzf "$archive" "$root/$2" | cmp - "/usr/share/licenses/$3"
}

check_notice ffmpeg-*.tar.gz COPYING.GPLv2 FFmpeg-LICENSE
check_notice SVT-AV1-v*.tar.gz LICENSE.md SVT-AV1-LICENSE
check_notice SVT-AV1-v*.tar.gz PATENTS.md SVT-AV1-PATENTS
check_notice opus-*.tar.gz COPYING Opus-LICENSE

test -s Dockerfile
test -s scripts/build.sh
test -s ubuntu-packages.tsv

mkvtoolnix_version=$(dpkg-query -W -f='${source:Version}' mkvtoolnix)
mkvtoolnix_version=${mkvtoolnix_version#*:}
cd mkvtoolnix
test -s "mkvtoolnix_${mkvtoolnix_version}.dsc"
awk '
    /^Checksums-Sha256:/ { section = 1; next }
    section && /^ / { print $1 "  " $3; next }
    section { exit }
' "mkvtoolnix_${mkvtoolnix_version}.dsc" | sha256sum -c -
