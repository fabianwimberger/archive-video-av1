"""Cluster status API endpoints."""

import time

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.models.schemas import ClusterStatusResponse
from app.services.distributed import LeaderRequestError, distributed_service
from app.services.job_queue import job_queue

router = APIRouter()


@router.get("/status", response_model=ClusterStatusResponse)
async def get_cluster_status(
    cluster: bool = Query(True, description="Read selected leader state")
):
    """Get distributed processing status."""
    if cluster and distributed_service.should_use_leader():
        try:
            return await distributed_service.request_leader("GET", "/api/cluster/status")
        except LeaderRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    queue_status = await job_queue.get_queue_status_async()
    now = time.monotonic()
    return {
        "enabled": settings.DISTRIBUTED_ENABLED,
        "node_id": settings.DISTRIBUTED_NODE_ID,
        "node_name": settings.DISTRIBUTED_NODE_NAME,
        "public_url": distributed_service.public_url,
        "leader_url": distributed_service.leader_url or None,
        "is_leader": distributed_service.is_leader,
        "pending_count": queue_status["pending_count"],
        "active_job_id": queue_status["active_job_id"],
        "peers": [
            {
                "node_id": peer.node_id,
                "node_name": peer.node_name,
                "base_url": peer.base_url,
                "last_seen_seconds": now - peer.last_seen,
            }
            for peer in distributed_service.peers()
        ],
    }
