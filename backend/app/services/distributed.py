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
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.models.schemas import LedgerJob
from app.services.cluster_ledger import read_ledger, write_ledger
from app.services.lifecycle import INTERRUPTED_ERROR_PREFIX

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "processing")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
REASSIGNED_ERROR = "No longer assigned to this node by the cluster leader"


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
        self._unreachable_since: dict[int, float] = {}
        self._log_synced_at: dict[int, float] = {}
        self._departed: set[str] = set()
        self._last_heard: dict[str, float] = {}
        self._term: Optional[int] = None
        self._ledger_key: Optional[tuple] = None
        self._ledger_changed_at = time.monotonic()

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

        # Heartbeats have stopped, so peers cannot re-add this node after the notice.
        await self._announce_leave()

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

    @property
    def holds_queue(self) -> bool:
        return self._term is not None and self.is_leader

    async def ensure_local_cluster_job_ids(self) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Job).where(Job.cluster_job_id.is_(None)))
            jobs = list(result.scalars().all())
            for job in jobs:
                job.cluster_origin_node_id = self.node_id  # type: ignore[assignment]
                job.cluster_origin_job_id = job.id  # type: ignore[assignment]
                job.cluster_job_id = f"{self.node_id}:{job.id}"  # type: ignore[assignment]
            if jobs:
                await db.commit()

    async def own_queue(self, websocket_manager) -> bool:
        """Take over or keep the cluster queue; False while another node owns it."""
        try:
            ledger = await read_ledger()
        except (OSError, ValueError) as exc:
            logger.error("Cluster ledger unreadable: %s", exc)
            return False
        self._observe_ledger(ledger)
        if not self.leader_is_stable():
            return False

        if self._term is not None:
            if self._superseded(ledger):
                return False
            return True
        if not self._may_take_over(ledger):
            return False

        if ledger is not None and not await self._load_ledger(ledger):
            return False
        self._term = int((ledger or {}).get("term", 0)) + 1
        logger.info("Took over the cluster queue (term %s)", self._term)
        await self.publish_queue()
        if websocket_manager:
            await websocket_manager.broadcast({"type": "queue_update"})
        return True

    def _observe_ledger(self, ledger: Optional[dict]) -> None:
        key = (ledger or {}).get("term"), (ledger or {}).get("updated_at")
        if key != self._ledger_key:
            self._ledger_key = key
            self._ledger_changed_at = time.monotonic()

    def _may_take_over(self, ledger: Optional[dict]) -> bool:
        if ledger is None:
            return True
        owner = ledger.get("leader_node_id")
        if owner == self.node_id or owner in self._departed:
            return True
        # The owner rewrites the ledger every heartbeat; silence means it is gone.
        return (
            time.monotonic() - self._ledger_changed_at
            >= settings.DISTRIBUTED_PEER_TTL_SECONDS
        )

    def _superseded(self, ledger: Optional[dict]) -> bool:
        if ledger is None or self._term is None:
            return False
        term = int(ledger.get("term", 0))
        if term > self._term or (
            term == self._term and ledger.get("leader_node_id") != self.node_id
        ):
            logger.warning(
                "%s took over the cluster queue; stepping down",
                ledger.get("leader_node_id"),
            )
            self._term = None
            return True
        return False

    async def _load_ledger(self, ledger: dict) -> bool:
        from app.services.job_queue import job_queue

        entries = {
            entry.cluster_job_id: entry
            for entry in (LedgerJob.model_validate(raw) for raw in ledger["jobs"])
        }

        # A local encode the cluster queue does not assign to this node must
        # finish cancelling before its row can take the queue's state.
        running_id = job_queue.current_job_id
        if running_id is not None:
            async with AsyncSessionLocal() as db:
                running = await db.get(Job, running_id)
            entry = entries.get(cast(str, running.cluster_job_id)) if running else None
            if running is not None and (
                entry is None or entry.assigned_worker_id != self.node_id
            ):
                await job_queue.cancel_current_job(REASSIGNED_ERROR)
                for _ in range(50):
                    if job_queue.current_job_id != running_id:
                        break
                    await asyncio.sleep(0.2)
                else:
                    return False

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(
                    or_(
                        Job.cluster_job_id.in_(entries),
                        Job.status.in_(ACTIVE_STATUSES),
                    )
                )
            )
            local = {
                cast(str, job.cluster_job_id): job for job in result.scalars().all()
            }

            for cluster_job_id, job in local.items():
                if cluster_job_id not in entries and job.status in ACTIVE_STATUSES:
                    await db.delete(job)

            for entry in entries.values():
                existing = local.get(entry.cluster_job_id)
                if entry.assigned_worker_id == self.node_id and existing is not None:
                    if existing.status == "completed" or (
                        job_queue.current_job_id == existing.id
                    ):
                        continue
                job = existing if existing is not None else Job()
                if existing is None:
                    db.add(job)
                self._apply_entry(job, entry)
                if entry.assigned_worker_id == self.node_id:
                    # Its encode on this node did not survive the restart.
                    self._requeue_job(job, "node restart")
            await db.commit()
        return True

    async def publish_queue(self) -> None:
        if not self.holds_queue:
            return
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job)
                .where(Job.status.in_(ACTIVE_STATUSES))
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

        try:
            if self._superseded(await read_ledger()):
                return
            ledger = {
                "term": self._term,
                "leader_node_id": self.node_id,
                "leader_url": self.public_url,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "jobs": jobs,
            }
            await write_ledger(ledger)
        except (OSError, ValueError) as exc:
            logger.error("Cluster ledger write failed: %s", exc)
            return
        self._observe_ledger(ledger)

    async def sync_remote_jobs(self, websocket_manager) -> int:
        """Pull state of delegated jobs; returns how many left the active queue."""
        if not settings.DISTRIBUTED_ENABLED or not self.holds_queue:
            return 0

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(
                    Job.status == "processing",
                    Job.assigned_worker_url.is_not(None),
                    Job.assigned_worker_id != self.node_id,
                )
            )
            jobs = list(result.scalars().all())
            active_ids = {cast(int, job.id) for job in jobs}
            for tracked in (self._unreachable_since, self._log_synced_at):
                for stale_id in tracked.keys() - active_ids:
                    del tracked[stale_id]

            changed = 0
            for job in jobs:
                if not job.assigned_worker_url:
                    continue

                assigned_worker_url = cast(str, job.assigned_worker_url)
                if job.remote_job_id is None:
                    peer = PeerNode(
                        str(job.assigned_worker_id),
                        str(job.assigned_worker_name),
                        assigned_worker_url,
                        0,
                    )
                    try:
                        job.remote_job_id = await self._create_remote_job(peer, job)
                    except ValueError as exc:
                        job.status = "failed"
                        job.error_message = str(exc)
                        job.completed_at = datetime.now(timezone.utc)
                        changed += 1
                        continue
                    if job.remote_job_id is None and self._worker_lost(job):
                        self._requeue_job(job, "worker unreachable")
                        changed += 1
                    continue
                remote_job_id = cast(int, job.remote_job_id)
                now = time.monotonic()
                include_log = (
                    now - self._log_synced_at.get(cast(int, job.id), 0.0)
                    >= settings.DISTRIBUTED_HEARTBEAT_SECONDS
                )
                remote_job = await self._get_remote_job(
                    assigned_worker_url, remote_job_id, include_log
                )
                if remote_job is None:
                    # A network outage alone does not establish that the encoder
                    # stopped; only a worker that also stopped heartbeating is lost.
                    if self._worker_lost(job):
                        self._requeue_job(job, "worker unreachable")
                        changed += 1
                    continue
                self._unreachable_since.pop(cast(int, job.id), None)

                remote_status = remote_job.get("status")
                if remote_status == "failed" and str(
                    remote_job.get("error_message") or ""
                ).startswith(INTERRUPTED_ERROR_PREFIX):
                    self._requeue_job(job, "worker restarted")
                    changed += 1
                    continue
                if remote_status in TERMINAL_STATUSES and not include_log:
                    remote_job = (
                        await self._get_remote_job(
                            assigned_worker_url, remote_job_id, True
                        )
                        or remote_job
                    )
                    include_log = True
                if include_log:
                    self._log_synced_at[cast(int, job.id)] = now
                    job.log = remote_job.get("log", job.log)  # type: ignore[assignment]

                job.progress_percent = remote_job.get(  # type: ignore[assignment]
                    "progress_percent", job.progress_percent
                )
                job.eta_seconds = remote_job.get("eta_seconds")  # type: ignore[assignment]
                job.current_fps = remote_job.get("current_fps")  # type: ignore[assignment]

                if remote_status in TERMINAL_STATUSES:
                    changed += 1
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
                                "current_log": job.log if include_log else None,
                                "stage": "remote",
                                "status": f"Processing on {job.assigned_worker_name}",
                            },
                        }
                    )

            await db.commit()

        if changed and websocket_manager:
            await websocket_manager.broadcast({"type": "queue_update"})
        return changed

    def _worker_lost(self, job: Job) -> bool:
        if any(
            peer.node_id == job.assigned_worker_id
            or peer.base_url == job.assigned_worker_url
            for peer in self.peers()
        ):
            self._unreachable_since.pop(cast(int, job.id), None)
            return False
        now = time.monotonic()
        since = self._last_heard.get(
            str(job.assigned_worker_id),
            self._unreachable_since.setdefault(cast(int, job.id), now),
        )
        return now - since >= settings.DISTRIBUTED_WORKER_TIMEOUT_SECONDS

    def _requeue_job(self, job: Job, reason: str) -> None:
        worker = job.assigned_worker_name or job.assigned_worker_id or self.node_name
        if job.id is not None:
            self._unreachable_since.pop(cast(int, job.id), None)
        attempts = (job.requeue_count or 0) + 1
        if attempts > settings.DISTRIBUTED_MAX_REQUEUES:
            logger.warning(
                "Giving up on job %s after %s interrupted attempts", job.id, attempts
            )
            job.status = "failed"  # type: ignore[assignment]
            job.error_message = f"Gave up after {attempts} interrupted attempts (last: {reason} on {worker})"  # type: ignore[assignment]
            job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            return

        logger.warning("Requeueing job %s from %s: %s", job.id, worker, reason)
        job.requeue_count = attempts  # type: ignore[assignment]
        job.log = f"Requeued after {reason} on {worker}\n"  # type: ignore[assignment]
        job.status = "pending"  # type: ignore[assignment]
        job.completed_at = None  # type: ignore[assignment]
        job.assigned_worker_id = None  # type: ignore[assignment]
        job.assigned_worker_name = None  # type: ignore[assignment]
        job.assigned_worker_url = None  # type: ignore[assignment]
        job.remote_job_id = None  # type: ignore[assignment]
        job.started_at = None  # type: ignore[assignment]
        job.progress_percent = 0.0  # type: ignore[assignment]
        job.eta_seconds = None  # type: ignore[assignment]
        job.current_fps = None  # type: ignore[assignment]
        job.error_message = None  # type: ignore[assignment]

    async def handle_peer_leave(self, node_id: str, websocket_manager) -> int:
        self._peers.pop(node_id, None)
        self._departed.add(node_id)
        if self._leader_id == node_id:
            self._leader_id = None
        if not self.holds_queue:
            return 0

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(
                    Job.status == "processing",
                    Job.assigned_worker_id == node_id,
                )
            )
            jobs = list(result.scalars().all())
            for job in jobs:
                self._requeue_job(job, "worker shut down")
            if jobs:
                await db.commit()

        if jobs and websocket_manager:
            await websocket_manager.broadcast({"type": "queue_update"})
        return len(jobs)

    async def _announce_leave(self) -> None:
        if self._client is None:
            return
        await asyncio.gather(
            *(self._send_leave(peer) for peer in self._peer_candidates())
        )

    async def _send_leave(self, peer: PeerNode) -> None:
        assert self._client is not None
        try:
            response = await self._client.post(
                f"{peer.base_url}/api/cluster/leave",
                json={"node_id": self.node_id},
                timeout=2.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Leave notice failed for %s: %s", peer.base_url, exc)

    async def reconcile_with_leader(self) -> None:
        """Stop local work the leader no longer assigns here; drop old leader rows."""
        if (
            not settings.DISTRIBUTED_ENABLED
            or self.is_leader
            or not self.leader_is_stable()
        ):
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).where(Job.status.in_(ACTIVE_STATUSES))
            )
            jobs = list(result.scalars().all())
        if not jobs:
            return

        own = [job for job in jobs if job.assigned_worker_id == self.node_id]
        try:
            data = await self.request_leader(
                "POST",
                "/api/cluster/reconcile",
                json_body={"cluster_job_ids": [job.cluster_job_id for job in own]},
            )
        except LeaderRequestError as exc:
            logger.debug("Reconcile with leader failed: %s", exc)
            return

        verdicts = data.get("jobs", {})
        stop_ids = [
            cast(int, job.id)
            for job in own
            if verdicts.get(job.cluster_job_id)
            != {"status": "processing", "assigned_worker_id": self.node_id}
        ]
        leftover_ids = [
            cast(int, job.id) for job in jobs if job.assigned_worker_id != self.node_id
        ]
        if leftover_ids:
            await self._drop_leftover_jobs(leftover_ids)
        if stop_ids:
            await self._stop_reassigned_jobs(stop_ids)

    async def _drop_leftover_jobs(self, job_ids: list[int]) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(Job).where(
                    Job.id.in_(job_ids),
                    Job.status.in_(ACTIVE_STATUSES),
                    or_(
                        Job.assigned_worker_id.is_(None),
                        Job.assigned_worker_id != self.node_id,
                    ),
                )
            )
            await db.commit()
        logger.info("Dropped %s leftover queue row(s)", len(job_ids))

    async def _stop_reassigned_jobs(self, job_ids: list[int]) -> None:
        # Running this work here would duplicate or resurrect an encode.
        from app.services.job_queue import job_queue

        async with AsyncSessionLocal() as db:
            for job_id in job_ids:
                logger.warning("Stopping job %s: %s", job_id, REASSIGNED_ERROR)
                if job_queue.current_job_id == job_id:
                    await job_queue.cancel_current_job(REASSIGNED_ERROR)
                    continue
                await db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status.in_(ACTIVE_STATUSES),
                    )
                    .values(
                        status="cancelled",
                        error_message=REASSIGNED_ERROR,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
            await db.commit()

    async def reconcile_follower_jobs(
        self, db: AsyncSession, cluster_job_ids: list[str]
    ) -> Optional[dict[str, dict]]:
        if not self.holds_queue:
            return None
        result = await db.execute(
            select(Job).where(Job.cluster_job_id.in_(cluster_job_ids))
        )
        return {
            cast(str, job.cluster_job_id): {
                "status": job.status,
                "assigned_worker_id": job.assigned_worker_id,
            }
            for job in result.scalars().all()
        }

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
            "assigned_worker_url": (
                self.public_url
                if job.assigned_worker_id == self.node_id
                else job.assigned_worker_url
            ),
            "remote_job_id": (
                job.id
                if job.status == "processing" and job.assigned_worker_id == self.node_id
                else job.remote_job_id
            ),
            "requeue_count": job.requeue_count or 0,
            "progress_percent": job.progress_percent or 0.0,
            "eta_seconds": job.eta_seconds,
            "current_fps": job.current_fps,
            "created_at": _format_datetime(cast(Optional[datetime], job.created_at)),
            "started_at": _format_datetime(cast(Optional[datetime], job.started_at)),
            "completed_at": _format_datetime(
                cast(Optional[datetime], job.completed_at)
            ),
            "error_message": job.error_message,
            "source_size_bytes": job.source_size_bytes,
            "output_size_bytes": job.output_size_bytes,
        }

    def _apply_entry(self, job: Job, entry: LedgerJob) -> None:
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
            "requeue_count",
            "progress_percent",
            "eta_seconds",
            "current_fps",
            "error_message",
            "source_size_bytes",
            "output_size_bytes",
        ):
            setattr(job, field, getattr(entry, field))
        job.created_at = entry.created_at or datetime.now(timezone.utc)  # type: ignore[assignment]
        job.started_at = entry.started_at  # type: ignore[assignment]
        job.completed_at = entry.completed_at  # type: ignore[assignment]

    async def delegate_pending_jobs(self, websocket_manager) -> int:
        if not settings.DISTRIBUTED_ENABLED or not self.holds_queue:
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
                )
                .order_by(Job.queue_position.asc().nullslast(), Job.created_at.asc())
                .limit(len(available_peers))
            )
            jobs = list(result.scalars().all())

            for job, peer in zip(jobs, available_peers):
                job.status = "processing"  # type: ignore[assignment]
                job.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                job.assigned_worker_id = peer.node_id  # type: ignore[assignment]
                job.assigned_worker_name = peer.node_name  # type: ignore[assignment]
                job.assigned_worker_url = peer.base_url  # type: ignore[assignment]
                await db.commit()
                await self.publish_queue()
                try:
                    job.remote_job_id = await self._create_remote_job(peer, job)  # type: ignore[assignment]
                except ValueError as exc:
                    job.status = "failed"  # type: ignore[assignment]
                    job.error_message = str(exc)  # type: ignore[assignment]
                    job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                await db.commit()
                delegated += 1

                if websocket_manager:
                    await websocket_manager.broadcast(
                        {
                            "type": "job_status",
                            "job_id": job.id,
                            "status": job.status,
                            "error": job.error_message,
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
                continue
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
        self, base_url: str, remote_job_id: int, include_log: bool = True
    ) -> Optional[dict]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)

        try:
            response = await self._client.get(
                f"{base_url}/api/jobs/{remote_job_id}",
                params={
                    "cluster": "false",
                    "include_log": "true" if include_log else "false",
                },
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
            "cluster_job_id": job.cluster_job_id,
            "cluster_origin_node_id": job.cluster_origin_node_id,
            "cluster_origin_job_id": job.cluster_origin_job_id,
        }

        try:
            response = await self._client.post(
                f"{peer.base_url}/api/jobs", json=payload
            )
            response.raise_for_status()
            job_ids = response.json().get("job_ids", [])
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                raise ValueError(_response_detail(exc.response)) from exc
            return None
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
        sock = self._socket
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _on_readable() -> None:
            try:
                data, _addr = sock.recvfrom(65535)
            except OSError:
                return
            queue.put_nowait(data)

        loop.add_reader(sock.fileno(), _on_readable)
        try:
            while self._running:
                data = await queue.get()
                self._handle_discovery_packet(data)
        finally:
            loop.remove_reader(sock.fileno())

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
        self._departed.discard(peer.node_id)
        self._last_heard[peer.node_id] = peer.last_seen
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
