"""LAN peer discovery and remote job delegation."""

import asyncio
import hashlib
import json
import logging
import socket
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, cast

import httpx
from httpx._types import QueryParamTypes
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job

logger = logging.getLogger(__name__)


@dataclass
class PeerNode:
    node_id: str
    node_name: str
    base_url: str
    last_seen: float


class DistributedService:
    """Coordinates peer discovery and remote job execution."""

    def __init__(self) -> None:
        self._peers: dict[str, PeerNode] = {}
        self._socket: Optional[socket.socket] = None
        self._tasks: list[asyncio.Task] = []
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._leader_id: Optional[str] = None
        self._leader_since = time.monotonic()

    @property
    def node_id(self) -> str:
        return settings.DISTRIBUTED_NODE_ID

    @property
    def node_name(self) -> str:
        return settings.DISTRIBUTED_NODE_NAME

    @property
    def public_url(self) -> str:
        configured_url = settings.DISTRIBUTED_PUBLIC_URL.strip().rstrip("/")
        if configured_url:
            return configured_url
        return f"http://{_detect_local_ip()}:8000"

    @property
    def leader_url(self) -> str:
        configured_url = settings.DISTRIBUTED_LEADER_URL.strip().rstrip("/")
        if configured_url:
            return configured_url
        return self._elected_leader().base_url

    @property
    def is_leader(self) -> bool:
        configured_url = settings.DISTRIBUTED_LEADER_URL.strip().rstrip("/")
        if configured_url:
            return configured_url == self.public_url
        return self._elected_leader().node_id == self.node_id

    def should_use_leader(self) -> bool:
        return settings.DISTRIBUTED_ENABLED and not self.is_leader

    async def start(self) -> None:
        if self._running or not settings.DISTRIBUTED_ENABLED:
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=5.0)
        await self.ensure_local_cluster_job_ids()

        try:
            self._socket = self._build_socket()
        except OSError as exc:
            logger.warning("Multicast discovery unavailable: %s", exc)
        else:
            self._tasks.extend(
                [
                    asyncio.create_task(self._broadcast_loop()),
                    asyncio.create_task(self._listen_loop()),
                ]
            )
        self._tasks.append(asyncio.create_task(self._probe_loop()))
        logger.info("Distributed processing enabled as %s", self.node_id)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

        if self._socket:
            self._socket.close()
            self._socket = None

        if self._client:
            await self._client.aclose()
            self._client = None

    def peers(self) -> list[PeerNode]:
        now = time.monotonic()
        fresh_peers = []
        stale_ids = []
        for node_id, peer in self._peers.items():
            if now - peer.last_seen <= settings.DISTRIBUTED_PEER_TTL_SECONDS:
                fresh_peers.append(peer)
            else:
                stale_ids.append(node_id)

        for node_id in stale_ids:
            self._peers.pop(node_id, None)

        return sorted(fresh_peers, key=lambda peer: peer.node_name)

    def cluster_nodes(self) -> list[PeerNode]:
        return sorted(
            [
                PeerNode(
                    node_id=self.node_id,
                    node_name=self.node_name,
                    base_url=self.public_url,
                    last_seen=time.monotonic(),
                ),
                *self.peers(),
            ],
            key=lambda peer: peer.node_id,
        )

    def _elected_leader(self) -> PeerNode:
        nodes = self.cluster_nodes()
        if self._leader_id:
            for node in nodes:
                if node.node_id == self._leader_id:
                    return node

        leader = max(nodes, key=lambda node: self._election_key(node.node_id))
        self._set_leader(leader.node_id)
        return leader

    def _election_key(self, node_id: str) -> str:
        return hashlib.sha256(node_id.encode("utf-8")).hexdigest()

    def leader_age_seconds(self) -> float:
        self._elected_leader()
        return time.monotonic() - self._leader_since

    def leader_is_stable(self) -> bool:
        return self.leader_age_seconds() >= settings.DISTRIBUTED_PEER_TTL_SECONDS

    def _set_leader(self, node_id: str) -> None:
        if self._leader_id != node_id:
            self._leader_since = time.monotonic()
        self._leader_id = node_id

    def _current_leader_is_fresh(self) -> bool:
        if self._leader_id == self.node_id:
            return True
        return any(peer.node_id == self._leader_id for peer in self.peers())

    def _peer_candidates(self) -> list[PeerNode]:
        candidates = {peer.base_url: peer for peer in self.peers()}
        for base_url in settings.DISTRIBUTED_PEERS:
            if base_url == self.public_url:
                continue
            candidates.setdefault(
                base_url,
                PeerNode(
                    node_id=base_url,
                    node_name=base_url,
                    base_url=base_url,
                    last_seen=0,
                ),
            )

        return sorted(candidates.values(), key=lambda peer: peer.node_name)

    def _peer_is_fresh(self, base_url: str) -> bool:
        return any(peer.base_url == base_url for peer in self.peers())

    async def ensure_local_cluster_job_ids(self) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(
                    Job.cluster_job_id.is_(None),
                    Job.is_cluster_replica.is_(False),
                )
            )
            jobs = list(result.scalars().all())
            for job in jobs:
                job.cluster_origin_node_id = self.node_id  # type: ignore[assignment]
                job.cluster_origin_job_id = job.id  # type: ignore[assignment]
                job.cluster_job_id = f"{self.node_id}:{job.id}"  # type: ignore[assignment]
            if jobs:
                await db.commit()

    async def sync_remote_jobs(self, websocket_manager) -> None:
        if not settings.DISTRIBUTED_ENABLED:
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(
                    Job.status == "processing",
                    Job.remote_job_id.is_not(None),
                    Job.is_cluster_replica.is_(False),
                )
            )
            jobs = list(result.scalars().all())

            for job in jobs:
                if not job.assigned_worker_url or not job.remote_job_id:
                    continue

                assigned_worker_url = cast(str, job.assigned_worker_url)
                remote_job_id = cast(int, job.remote_job_id)
                remote_job = await self._get_remote_job(
                    assigned_worker_url, remote_job_id
                )
                if remote_job is None:
                    if self._peer_is_fresh(assigned_worker_url):
                        continue
                    await self._requeue_remote_job(db, job)
                    if websocket_manager:
                        await websocket_manager.broadcast(
                            {
                                "type": "job_status",
                                "job_id": job.id,
                                "status": "pending",
                                "error": None,
                            }
                        )
                    continue

                job.progress_percent = remote_job.get(  # type: ignore[assignment]
                    "progress_percent", job.progress_percent
                )
                job.eta_seconds = remote_job.get("eta_seconds")  # type: ignore[assignment]
                job.current_fps = remote_job.get("current_fps")  # type: ignore[assignment]
                job.log = remote_job.get("log", job.log)  # type: ignore[assignment]

                remote_status = remote_job.get("status")
                if remote_status in {"completed", "failed", "cancelled"}:
                    job.status = remote_status  # type: ignore[assignment]
                    job.completed_at = _parse_datetime(  # type: ignore[assignment]
                        remote_job.get("completed_at")
                    )
                    job.error_message = remote_job.get("error_message")  # type: ignore[assignment]
                    job.source_size_bytes = remote_job.get(  # type: ignore[assignment]
                        "source_size_bytes"
                    )
                    job.output_size_bytes = remote_job.get(  # type: ignore[assignment]
                        "output_size_bytes"
                    )

                    if websocket_manager:
                        await websocket_manager.broadcast(
                            {
                                "type": "job_status",
                                "job_id": job.id,
                                "status": remote_status,
                                "error": job.error_message,
                                "source_size_bytes": job.source_size_bytes,
                                "output_size_bytes": job.output_size_bytes,
                            }
                        )
                elif websocket_manager:
                    await websocket_manager.broadcast(
                        {
                            "type": "job_progress",
                            "job_id": job.id,
                            "data": {
                                "percent": job.progress_percent,
                                "fps": job.current_fps,
                                "eta_seconds": job.eta_seconds,
                                "current_log": job.log,
                                "stage": "remote",
                                "status": f"Processing on {job.assigned_worker_name}",
                            },
                        }
                    )

            await db.commit()

    async def replicate_queue(self) -> int:
        if not settings.DISTRIBUTED_ENABLED or not self.is_leader:
            return 0
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        payload = await self._replication_payload()
        replicated = 0
        for peer in self.peers():
            try:
                response = await self._client.post(
                    f"{peer.base_url}/api/cluster/replication",
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.debug("Queue replication failed for %s: %s", peer.base_url, exc)
                continue
            replicated += 1
        return replicated

    async def _replication_payload(self) -> dict:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job)
                .where(
                    Job.status.in_(["pending", "processing"]),
                    Job.is_cluster_replica.is_(False),
                )
                .order_by(Job.queue_position.asc().nullslast(), Job.created_at.asc())
            )
            jobs = []
            for job in result.scalars().all():
                if not job.cluster_job_id:
                    job.cluster_origin_node_id = self.node_id  # type: ignore[assignment]
                    job.cluster_origin_job_id = job.id  # type: ignore[assignment]
                    job.cluster_job_id = f"{self.node_id}:{job.id}"  # type: ignore[assignment]
                jobs.append(self._serialize_job(job))
            await db.commit()

        return {
            "leader_node_id": self.node_id,
            "leader_url": self.public_url,
            "leader_age_seconds": self.leader_age_seconds(),
            "jobs": jobs,
        }

    async def apply_queue_replication(self, db: AsyncSession, payload) -> int:
        leader_node_id = payload.leader_node_id
        if leader_node_id == self.node_id:
            return 0

        self._remember_reported_leader(
            {
                "node_id": leader_node_id,
                "leader_url": payload.leader_url,
                "is_leader": True,
                "leader_age_seconds": payload.leader_age_seconds,
            }
        )
        if self.is_leader:
            return 0

        incoming_ids = {job.cluster_job_id for job in payload.jobs}
        if incoming_ids:
            existing_result = await db.execute(
                select(Job).where(Job.cluster_job_id.in_(incoming_ids))
            )
            existing = {
                job.cluster_job_id: job for job in existing_result.scalars().all()
            }
        else:
            existing = {}

        applied = 0
        for replica in payload.jobs:
            job = existing.get(replica.cluster_job_id)
            if job is None:
                job = Job()
                db.add(job)
            self._apply_replica(job, replica)
            applied += 1

        stale_query = delete(Job).where(Job.is_cluster_replica.is_(True))
        if incoming_ids:
            stale_query = stale_query.where(~Job.cluster_job_id.in_(incoming_ids))
        await db.execute(stale_query)
        await db.commit()
        return applied

    async def promote_replicated_jobs(self, websocket_manager) -> int:
        if (
            not settings.DISTRIBUTED_ENABLED
            or not self.is_leader
            or not self.leader_is_stable()
        ):
            return 0

        async with AsyncSessionLocal() as db:
            jobs = await self._promote_replicated_jobs(db)
            if jobs:
                await db.commit()

        if jobs and websocket_manager:
            await websocket_manager.broadcast({"type": "queue_update"})
        return len(jobs)

    async def _promote_replicated_jobs(self, db: AsyncSession) -> list[Job]:
        result = await db.execute(
            select(Job).where(
                Job.is_cluster_replica.is_(True),
                Job.status.in_(["pending", "processing"]),
            )
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            if job.status == "processing" and job.remote_job_id is None:
                await self._requeue_remote_job(db, job)
            job.is_cluster_replica = False  # type: ignore[assignment]
        return jobs

    def _serialize_job(self, job: Job) -> dict:
        return {
            "cluster_job_id": job.cluster_job_id,
            "cluster_origin_node_id": job.cluster_origin_node_id or self.node_id,
            "cluster_origin_job_id": job.cluster_origin_job_id or job.id,
            "source_file": job.source_file,
            "output_file": job.output_file,
            "preset_id": job.preset_id,
            "preset_name_snapshot": job.preset_name_snapshot,
            "settings": job.settings or "{}",
            "notes": job.notes,
            "queue_position": job.queue_position,
            "status": job.status,
            "assigned_worker_id": job.assigned_worker_id,
            "assigned_worker_name": job.assigned_worker_name,
            "assigned_worker_url": job.assigned_worker_url,
            "remote_job_id": job.remote_job_id,
            "progress_percent": job.progress_percent or 0.0,
            "eta_seconds": job.eta_seconds,
            "current_fps": job.current_fps,
            "created_at": _format_datetime(cast(Optional[datetime], job.created_at)),
            "started_at": _format_datetime(cast(Optional[datetime], job.started_at)),
            "completed_at": _format_datetime(
                cast(Optional[datetime], job.completed_at)
            ),
            "error_message": job.error_message,
            "log": job.log or "",
            "source_size_bytes": job.source_size_bytes,
            "output_size_bytes": job.output_size_bytes,
        }

    def _apply_replica(self, job: Job, replica) -> None:
        for field in (
            "cluster_job_id",
            "cluster_origin_node_id",
            "cluster_origin_job_id",
            "source_file",
            "output_file",
            "preset_id",
            "preset_name_snapshot",
            "settings",
            "notes",
            "queue_position",
            "status",
            "assigned_worker_id",
            "assigned_worker_name",
            "assigned_worker_url",
            "remote_job_id",
            "progress_percent",
            "eta_seconds",
            "current_fps",
            "error_message",
            "log",
            "source_size_bytes",
            "output_size_bytes",
        ):
            setattr(job, field, getattr(replica, field))
        job.created_at = replica.created_at or datetime.now(timezone.utc)  # type: ignore[assignment]
        job.started_at = replica.started_at  # type: ignore[assignment]
        job.completed_at = replica.completed_at  # type: ignore[assignment]
        job.is_cluster_replica = True  # type: ignore[assignment]

    async def _requeue_remote_job(self, db, job: Job) -> None:
        result = await db.execute(
            select(func.max(Job.queue_position)).where(
                Job.status == "pending",
                Job.is_cluster_replica.is_(False),
            )
        )
        max_pos = result.scalar() or 0
        logger.warning(
            "Requeued job %s after worker %s disappeared",
            job.id,
            job.assigned_worker_name or job.assigned_worker_url,
        )
        job.status = "pending"  # type: ignore[assignment]
        job.assigned_worker_id = None  # type: ignore[assignment]
        job.assigned_worker_name = None  # type: ignore[assignment]
        job.assigned_worker_url = None  # type: ignore[assignment]
        job.remote_job_id = None  # type: ignore[assignment]
        job.is_cluster_replica = False  # type: ignore[assignment]
        job.started_at = None  # type: ignore[assignment]
        job.queue_position = max_pos + 1  # type: ignore[assignment]
        job.progress_percent = 0.0  # type: ignore[assignment]
        job.eta_seconds = None  # type: ignore[assignment]
        job.current_fps = None  # type: ignore[assignment]

    async def delegate_pending_jobs(self, websocket_manager) -> int:
        if not settings.DISTRIBUTED_ENABLED:
            return 0

        available_peers = await self._available_peers()
        if not available_peers:
            return 0

        delegated = 0
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job)
                .where(
                    Job.status == "pending",
                    Job.assigned_worker_id.is_(None),
                    Job.is_cluster_replica.is_(False),
                )
                .order_by(Job.queue_position.asc().nullslast(), Job.created_at.asc())
                .limit(len(available_peers))
            )
            jobs = list(result.scalars().all())

            for job, peer in zip(jobs, available_peers):
                remote_job_id = await self._create_remote_job(peer, job)
                if remote_job_id is None:
                    continue

                job.status = "processing"  # type: ignore[assignment]
                job.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                job.assigned_worker_id = peer.node_id  # type: ignore[assignment]
                job.assigned_worker_name = peer.node_name  # type: ignore[assignment]
                job.assigned_worker_url = peer.base_url  # type: ignore[assignment]
                job.remote_job_id = remote_job_id  # type: ignore[assignment]
                delegated += 1

                if websocket_manager:
                    await websocket_manager.broadcast(
                        {
                            "type": "job_status",
                            "job_id": job.id,
                            "status": "processing",
                            "error": None,
                        }
                    )

            await db.commit()

        if delegated:
            logger.info("Delegated %s job(s) to cluster peers", delegated)
        return delegated

    async def cancel_remote_job(self, job: Job) -> bool:
        if not job.assigned_worker_url or not job.remote_job_id:
            return False
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        try:
            response = await self._client.delete(
                f"{job.assigned_worker_url}/api/jobs/{job.remote_job_id}",
                params={"cluster": "false"},
            )
            if response.status_code < 400 or response.status_code == 404:
                return True
        except httpx.HTTPError as exc:
            logger.warning("Failed to cancel remote job %s: %s", job.id, exc)

        for _ in range(3):
            await asyncio.sleep(1.0)
            assigned_worker_url = cast(str, job.assigned_worker_url)
            remote_job_id = cast(int, job.remote_job_id)
            remote_job = await self._get_remote_job(assigned_worker_url, remote_job_id)
            if remote_job is None:
                return True
            if remote_job.get("status") in {"completed", "failed", "cancelled"}:
                return True

        return False

    async def _available_peers(self) -> list[PeerNode]:
        available = []
        for peer in self._peer_candidates():
            status = await self._get_peer_status(peer)
            if not status:
                continue
            if not status.get("enabled"):
                continue
            if status.get("active_job_id") is None and status.get("pending_count") == 0:
                available.append(peer)
        return available

    async def _get_peer_status(self, peer: PeerNode) -> Optional[dict]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        try:
            response = await self._client.get(
                f"{peer.base_url}/api/cluster/status",
                params={"cluster": "false"},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Peer status unavailable for %s: %s", peer.base_url, exc)
            return None

        node_id = data.get("node_id")
        if node_id == self.node_id:
            return None
        peer.node_id = str(node_id or peer.node_id)
        peer.node_name = data.get("node_name") or peer.node_name
        peer.last_seen = time.monotonic()
        self._remember_peer(peer)
        self._remember_reported_leader(data)
        return data

    async def list_peer_jobs(self, params: dict[str, object]) -> list[dict]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        jobs = []
        for peer in self.peers():
            status = await self._get_peer_status(peer)
            if not status or not status.get("enabled"):
                continue

            try:
                response = await self._client.get(
                    f"{peer.base_url}/api/jobs",
                    params=cast(QueryParamTypes, params),
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("Peer jobs unavailable for %s: %s", peer.base_url, exc)
                continue

            for job in data.get("jobs", []):
                job.setdefault("cluster_node_id", peer.node_id)
                job.setdefault("cluster_node_name", peer.node_name)
                job.setdefault("cluster_node_url", peer.base_url)
                jobs.append(job)

        return jobs

    async def request_leader(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, object]] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        leader_url = self.leader_url
        if not leader_url:
            raise RuntimeError("Distributed leader URL is not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        try:
            response = await self._client.request(
                method,
                f"{leader_url}{path}",
                params=cast(QueryParamTypes, params),
                json=json_body,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = _response_detail(exc.response)
            raise LeaderRequestError(exc.response.status_code, detail) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LeaderRequestError(502, f"Leader request failed: {exc}") from exc

    async def clear_peer_jobs(self, path: str) -> int:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        deleted = 0
        for peer in self.peers():
            status = await self._get_peer_status(peer)
            if not status or not status.get("enabled"):
                continue

            try:
                response = await self._client.delete(
                    f"{peer.base_url}{path}",
                    params={"cluster": "false"},
                )
                response.raise_for_status()
                data = response.json() if response.content else {}
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("Peer clear failed for %s: %s", peer.base_url, exc)
                continue

            deleted += int(data.get("deleted_count") or 0)

        return deleted

    async def _get_remote_job(
        self, base_url: str, remote_job_id: int
    ) -> Optional[dict]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        try:
            response = await self._client.get(
                f"{base_url}/api/jobs/{remote_job_id}",
                params={"cluster": "false"},
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug(
                "Remote job %s unavailable from %s: %s",
                remote_job_id,
                base_url,
                exc,
            )
            return None

    async def _create_remote_job(self, peer: PeerNode, job: Job) -> Optional[int]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        payload = {
            "source_file": job.source_file,
            "settings": json.loads(cast(str, job.settings)) if job.settings else {},
            "notes": job.notes,
            "local_only": True,
        }

        try:
            response = await self._client.post(
                f"{peer.base_url}/api/jobs", json=payload
            )
            response.raise_for_status()
            job_ids = response.json().get("job_ids", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Failed to delegate job %s to %s: %s", job.id, peer.base_url, exc
            )
            return None

        return job_ids[0] if job_ids else None

    async def _broadcast_loop(self) -> None:
        assert self._socket is not None
        while self._running:
            payload = {
                "service": "archive-video-av1",
                "node_id": self.node_id,
                "node_name": self.node_name,
                "base_url": self.public_url,
                "leader_url": self.leader_url,
                "is_leader": self.is_leader,
                "leader_age_seconds": self.leader_age_seconds(),
            }
            try:
                self._socket.sendto(
                    json.dumps(payload).encode("utf-8"),
                    (
                        settings.DISTRIBUTED_DISCOVERY_GROUP,
                        settings.DISTRIBUTED_DISCOVERY_PORT,
                    ),
                )
            except OSError as exc:
                logger.debug("Cluster heartbeat failed: %s", exc)
            await asyncio.sleep(settings.DISTRIBUTED_HEARTBEAT_SECONDS)

    async def _probe_loop(self) -> None:
        while self._running:
            for peer in self._peer_candidates():
                await self._get_peer_status(peer)
            await asyncio.sleep(settings.DISTRIBUTED_HEARTBEAT_SECONDS)

    async def _listen_loop(self) -> None:
        assert self._socket is not None
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _on_readable() -> None:
            try:
                data, _addr = self._socket.recvfrom(65535)
            except OSError:
                return
            queue.put_nowait(data)

        loop.add_reader(self._socket.fileno(), _on_readable)
        try:
            while self._running:
                data = await queue.get()
                self._handle_discovery_packet(data)
        finally:
            loop.remove_reader(self._socket.fileno())

    def _handle_discovery_packet(self, data: bytes) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except ValueError:
            return

        if payload.get("service") != "archive-video-av1":
            return
        if payload.get("node_id") == self.node_id:
            return

        base_url = str(payload.get("base_url", "")).rstrip("/")
        node_id = str(payload.get("node_id", ""))
        if not base_url or not node_id:
            return

        self._remember_peer(
            PeerNode(
                node_id=node_id,
                node_name=str(payload.get("node_name") or node_id),
                base_url=base_url,
                last_seen=time.monotonic(),
            )
        )
        self._remember_reported_leader(payload)

    def _remember_peer(self, peer: PeerNode) -> None:
        if peer.node_id == self.node_id:
            return
        existing_key = peer.node_id
        if existing_key not in self._peers:
            existing_key = next(
                (
                    node_id
                    for node_id, existing in self._peers.items()
                    if existing.base_url == peer.base_url
                ),
                peer.node_id,
            )

        if existing_key != peer.node_id:
            self._peers.pop(existing_key, None)

        self._peers[peer.node_id] = peer

    def _remember_reported_leader(self, payload: dict) -> None:
        if settings.DISTRIBUTED_LEADER_URL.strip():
            return

        leader_url = str(payload.get("leader_url") or "").rstrip("/")
        if not leader_url:
            return

        leader_id = ""
        if payload.get("is_leader"):
            leader_id = str(payload.get("node_id") or "")
        elif leader_url == self.public_url:
            leader_id = self.node_id
        else:
            for peer in self.peers():
                if peer.base_url == leader_url:
                    leader_id = peer.node_id
                    break

        if not leader_id or leader_id == self._leader_id:
            return
        if payload.get("node_id") == self._leader_id and not payload.get("is_leader"):
            self._set_leader(leader_id)
            return
        if not self._current_leader_is_fresh():
            self._set_leader(leader_id)
            return

        reported_age = float(payload.get("leader_age_seconds") or 0)
        if payload.get("is_leader"):
            current_age = self.leader_age_seconds()
            if reported_age > current_age + 1 or (
                abs(reported_age - current_age) <= 1
                and self._leader_id is not None
                and self._election_key(leader_id) > self._election_key(self._leader_id)
            ):
                self._set_leader(leader_id)

    def _build_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", settings.DISTRIBUTED_DISCOVERY_PORT))
        group = socket.inet_aton(settings.DISTRIBUTED_DISCOVERY_GROUP)
        mreq = group + struct.pack("=I", socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setblocking(False)
        return sock


def _detect_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostname()
    finally:
        sock.close()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


distributed_service = DistributedService()


class LeaderRequestError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _response_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message")
        if detail:
            return str(detail)
    return str(data)
