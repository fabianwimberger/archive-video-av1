# Third-Party Licenses

This Docker image contains the following third-party software packages:

## Compiled from Source

### FFmpeg
- **License:** GPL-2.0-or-later
- **Source:** https://git.ffmpeg.org/ffmpeg.git
- **Description:** Audio/video processing toolkit
- **Note:** Compiled with `--enable-gpl --enable-static`. Source code for the exact version used is available at https://ffmpeg.org/releases/. A license notice is included in the image at `/usr/share/licenses/FFmpeg-LICENSE`.

### SVT-AV1
- **License:** BSD-2-Clause
- **Source:** https://gitlab.com/AOMediaCodec/SVT-AV1
- **Description:** AV1 video encoder

### Opus (libopus)
- **License:** BSD-3-Clause
- **Source:** https://opus-codec.org/downloads/
- **Description:** Audio codec

## System Packages (apt)

### MKVToolNix
- **License:** GPL-2.0-or-later
- **Source:** https://mkvtoolnix.download/source.html
- **Description:** Matroska container tools (mkvmerge, mkvpropedit)

### Python
- **License:** PSF-2.0
- **Source:** https://www.python.org/downloads/source/
- **Description:** Programming language runtime

## Python Dependencies

### FastAPI
- **License:** MIT
- **Source:** https://github.com/tiangolo/fastapi

### Uvicorn
- **License:** BSD-3-Clause
- **Source:** https://github.com/encode/uvicorn

### SQLAlchemy
- **License:** MIT
- **Source:** https://github.com/sqlalchemy/sqlalchemy

### aiosqlite
- **License:** MIT
- **Source:** https://github.com/omnilib/aiosqlite

### python-multipart
- **License:** Apache-2.0
- **Source:** https://github.com/Kludex/python-multipart

### websockets
- **License:** BSD-3-Clause
- **Source:** https://github.com/python-websockets/websockets

## Frontend Vendors (downloaded at build time)

### Bootstrap
- **License:** MIT
- **Source:** https://github.com/twbs/bootstrap

### Bootstrap Icons
- **License:** MIT
- **Source:** https://github.com/twbs/icons

## Base Image

### Ubuntu
- **License:** Various open-source licenses
- **Source:** https://ubuntu.com/
- **Description:** Linux distribution base image

## Source Code Availability

In compliance with GPL license requirements, source code for GPL-licensed packages can be obtained from:

1. **FFmpeg:** https://ffmpeg.org/releases/ (match the version in the Dockerfile `ARG FFMPEG_VERSION`)
2. **MKVToolNix:** https://mkvtoolnix.download/source.html
3. **Ubuntu packages:** https://packages.ubuntu.com/

## License Texts

- GPL-2.0: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
- BSD-2-Clause: https://opensource.org/licenses/BSD-2-Clause
- BSD-3-Clause: https://opensource.org/licenses/BSD-3-Clause
- MIT: https://opensource.org/licenses/MIT
- Apache-2.0: https://www.apache.org/licenses/LICENSE-2.0
- PSF-2.0: https://docs.python.org/3/license.html

## Trademarks

All trademarks, service marks, and trade names are the property of their respective owners.
