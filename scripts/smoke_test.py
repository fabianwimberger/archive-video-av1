import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from fastapi.testclient import TestClient


def wait_for_job(client: TestClient, job_id: int) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.1)
    raise RuntimeError("Conversion did not finish within 90 seconds")


def main() -> None:
    sample = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "backend" if (root / "backend").is_dir() else root))
    with tempfile.TemporaryDirectory(prefix="conversion-smoke-") as directory:
        work = Path(directory)
        videos = work / "videos"
        videos.mkdir()
        source = videos / "sample.mkv"
        shutil.copyfile(sample, source)
        os.environ.update(
            SOURCE_MOUNT=str(videos),
            TEMP_DIR=str(work / "temp"),
            DATABASE_PATH=str(work / "app.db"),
            DISTRIBUTED_ENABLED="false",
        )
        from app.main import app

        conversion = {
            "crf": 35,
            "encoder_preset": 13,
            "svt_params": "lp=2",
            "audio_bitrate": "96k",
            "skip_crop_detect": True,
            "max_resolution": 720,
        }
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/").status_code == 200
            assert len(client.get("/api/presets").json()) == 4
            created = client.post(
                "/api/jobs", json={"source_file": str(source), "settings": conversion}
            )
            created.raise_for_status()
            job = wait_for_job(client, created.json()["job_ids"][0])
            assert job["status"] == "completed", job["log"]
            output = Path(job["output_file"])
            assert (
                output.is_file() and output.stat().st_size == job["output_size_bytes"]
            )
            info = client.get("/api/files/info", params={"path": str(output)}).json()
            assert info["codec"] == "av1" and info["duration"] > 0
            assert not list(videos.glob(".*.lock"))
            from app.config import settings

            before = output.read_bytes()
            command = [
                settings.CONVERSION_WRAPPER_SCRIPT,
                str(source),
                str(output),
                "35",
                "13",
                "lp=2",
                "96k",
                "1",
                "720",
            ]
            duplicate = subprocess.run(
                command, capture_output=True, start_new_session=True, timeout=60
            )
            assert duplicate.returncode != 0 and output.read_bytes() == before

            fake_bin = work / "bin"
            fake_bin.mkdir()
            remuxer = fake_bin / "mkvmerge"
            remuxer.write_text('#!/bin/sh\nprintf partial > "$2"\nexit 2\n')
            remuxer.chmod(0o755)
            failed_output = videos / "failed_conv.mkv"
            command[2] = str(failed_output)
            failed_remux = subprocess.run(
                command,
                capture_output=True,
                start_new_session=True,
                timeout=60,
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            )
            assert failed_remux.returncode != 0 and not failed_output.exists()
            assert not list(videos.glob(".failed_conv.mkv.*.tmp"))
            assert (
                client.post(
                    "/api/jobs",
                    json={"source_file": str(source), "settings": conversion},
                ).status_code
                == 409
            )
            assert (
                client.delete(
                    "/api/files/converted", params={"path": str(source)}
                ).status_code
                == 400
            )
            assert (
                client.delete("/api/files", params={"path": str(source)}).status_code
                == 200
            )

            broken = videos / "broken.mkv"
            broken.write_bytes(b"not a video")
            created = client.post(
                "/api/jobs", json={"source_file": str(broken), "settings": conversion}
            )
            created.raise_for_status()
            failed = wait_for_job(client, created.json()["job_ids"][0])
            assert failed["status"] == "failed"
            assert not Path(failed["output_file"]).exists()

            client.post("/api/queue/pause").raise_for_status()
            pending = client.post(
                "/api/jobs", json={"source_file": str(broken), "settings": conversion}
            )
            pending.raise_for_status()
            pending_id = pending.json()["job_ids"][0]
            client.delete(f"/api/jobs/{pending_id}").raise_for_status()
            assert client.get(f"/api/jobs/{pending_id}").status_code == 404


if __name__ == "__main__":
    main()
