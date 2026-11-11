# Third-Party Licenses

This Docker image contains the following third-party software packages:

## Compiled from Source

### FFmpeg
- **License:** GPL-2.0-or-later
- **Source:** https://git.ffmpeg.org/ffmpeg.git
- **Description:** Audio/video processing toolkit
- **Note:** Compiled with `--enable-gpl --enable-static`. The image includes the full license at `/usr/share/licenses/FFmpeg-LICENSE`.

### SVT-AV1
- **License:** BSD-3-Clause-Clear and Alliance for Open Media Patent License 1.0
- **Source:** https://gitlab.com/AOMediaCodec/SVT-AV1
- **Description:** AV1 video encoder

### Opus (libopus)
- **License:** BSD-3-Clause
- **Source:** https://opus-codec.org/downloads/
- **Description:** Audio codec

## System Packages (apt)

### MKVToolNix
- **License:** GPL-2.0-or-later
- **Source:** Ubuntu source package included in the image, with its packaging and patches
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

## Source Included in the Docker Image

The image includes the exact FFmpeg, SVT-AV1 and Opus source archives, the Dockerfile and codec build script, and the Ubuntu source package for its installed MKVToolNix version. Extract them without starting the container. Replace `main` with the tag you use.

```sh
docker create --name archive-video-av1-source ghcr.io/fabianwimberger/archive-video-av1:main
docker cp archive-video-av1-source:/usr/share/source/archive-video-av1 ./archive-video-av1-source
docker rm archive-video-av1-source
```

The `ubuntu-packages.tsv` inventory records each installed Ubuntu package and its exact source package and version. Ubuntu publishes these sources on [Launchpad](https://launchpad.net/ubuntu/+source). Match the source name and version in the last two columns.

Full codec license texts are in `/usr/share/licenses/`. Ubuntu package notices are in `/usr/share/doc/` and shared license texts are in `/usr/share/common-licenses/`.

## License Texts

- GPL-2.0: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
- BSD-2-Clause: https://opensource.org/licenses/BSD-2-Clause
- BSD-3-Clause: https://opensource.org/licenses/BSD-3-Clause
- MIT: https://opensource.org/licenses/MIT
- Apache-2.0: https://www.apache.org/licenses/LICENSE-2.0
- PSF-2.0: https://docs.python.org/3/license.html

## Trademarks

All trademarks, service marks, and trade names are the property of their respective owners.
