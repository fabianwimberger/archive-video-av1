# Distributed Processing

Distributed mode lets several trusted LAN devices run the container and share AV1 jobs. Queue jobs from any node; non-leader nodes forward queue changes to the current leader, the leader delegates pending work to discovered idle peers, and every node shows the same cluster-wide active queue with worker assignments. By default, nodes use a deterministic tie-break when no leader is known, then keep the current leader until it disappears. Set `DISTRIBUTED_LEADER_URL` only when you want to pin one coordinator.

Cluster state is shown in the Active Queue panel and is also available at `/api/cluster/status`. Active job listings include peer jobs by default; pass `cluster=false` to `/api/jobs` when a node-local view is needed.

Only the leader changes the queue. It mirrors the queue to a ledger file in `DISTRIBUTED_STATE_DIR` after every change, and a node that becomes leader loads the queue from that ledger instead of its own database. A node that was offline therefore never resumes jobs that were deleted or finished elsewhere in the meantime, even when it comes back alone. While a new leader is taking over, queue changes are rejected with `503` for a few seconds.

A node that stops cleanly tells its peers it is leaving, so the next leader takes over and requeues the job it was running right away. If a node disappears without notice, the leader requeues its job once the node has been silent for `DISTRIBUTED_WORKER_TIMEOUT_SECONDS`. A job that keeps losing its worker is marked failed after `DISTRIBUTED_MAX_REQUEUES` attempts. Followers regularly confirm their local work with the leader and stop any encode the leader no longer assigns to them.

A worker that is unreachable but still heartbeating keeps its assigned jobs; lost dispatch responses are retried on that worker using the same job identity. Once a worker has been silent for `DISTRIBUTED_WORKER_TIMEOUT_SECONDS`, its jobs are requeued, and if it comes back still encoding, it stops as soon as it sees the job reassigned. While an output is being written or deleted, a hidden `.<name>_conv.mkv.lock` file next to it guards it against other nodes; the file is removed once the job ends.

Requirements:

- `docker-compose.cluster.yml` is not shipped in this repo - it's your own node-specific compose file (ports, hostnames, `DISTRIBUTED_*` env vars per node). Create it yourself before running the commands below.
- All participating nodes must mount the same media library at the same in-container `SOURCE_MOUNT` path.
- All nodes must run the same version, and the shared filesystem must support cross-host POSIX byte-range locks and hard links. NFSv4 does (keep the default `local_lock=none`); a node on the file server itself shares locks with NFS clients. SMB/CIFS mounts are not supported for multi-node setups.
- Converted files take the source file's mode and owner. When a node's mount maps users (NFS `root_squash`/`all_squash`, SMB `uid=`/`gid=`/`file_mode=`), the owner change may be refused; the job still succeeds and the file keeps the mount's owner, or `PUID`/`PGID` if the mount allows those.
- `DISTRIBUTED_STATE_DIR` (by default inside `SOURCE_MOUNT`) must resolve to the same shared, writable directory on every node.
- When upgrading from a version without the queue ledger, stop all nodes, upgrade them together, and start the previous leader first; wait for `queue.json` to appear in `DISTRIBUTED_STATE_DIR` before starting the others, or let the queue run empty before upgrading.
- Every node must be reachable from every other node through `DISTRIBUTED_PUBLIC_URL`.
- For automatic leader election, leave `DISTRIBUTED_LEADER_URL` empty on every node and use stable, unique `DISTRIBUTED_NODE_ID` values.
- The network must allow UDP multicast on `DISTRIBUTED_DISCOVERY_PORT`, or `DISTRIBUTED_PEERS` must list peer URLs explicitly.
- Docker host networking is the most reliable option for multicast discovery; otherwise use `DISTRIBUTED_PEERS`.
- For three or more nodes, set each node's `DISTRIBUTED_PEERS` to the comma-separated URLs of the other nodes.
- The service has no built-in authentication, so only enable distributed mode on a trusted network.

Compose node commands:

```bash
# Build the local node image with the distributed compose stack
make node-build

# Start the node with docker-compose.override.yml and docker-compose.cluster.yml
make node-up

# Recreate the node after code or image changes
make node-recreate

# Stop the node stack without deleting volumes
make node-down
```

`docker compose up -d` auto-loads `docker-compose.override.yml` only for the default compose stack. Distributed nodes must include `docker-compose.cluster.yml`, so use the node targets above or pass all compose files explicitly.

Example with explicit peers:

```bash
docker run -d \
  --name convert-service-node-a \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /mnt/media:/videos \
  -v archive-video-av1-node-a:/app/data \
  -e SOURCE_MOUNT=/videos \
  -e DISTRIBUTED_ENABLED=true \
  -e DISTRIBUTED_NODE_ID=notebook \
  -e DISTRIBUTED_NODE_NAME=notebook \
  -e DISTRIBUTED_PUBLIC_URL=http://192.168.1.10:8000 \
  -e DISTRIBUTED_PEERS=http://192.168.1.11:8000,http://192.168.1.12:8000 \
  ghcr.io/fabianwimberger/archive-video-av1:latest
```

If a worker disappears while it is processing a delegated job, the leader requeues that job after `DISTRIBUTED_WORKER_TIMEOUT_SECONDS`. If the leader disappears, the next elected leader waits until the ledger has gone quiet for `DISTRIBUTED_PEER_TTL_SECONDS`, takes over the queue from it and continues scheduling from there.

## Environment Variables

Enabled, node name, public URL, leader URL and peers can also be set on the Settings page; an environment variable wins and locks the field.

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTRIBUTED_ENABLED` | `false` | Enable LAN peer discovery and remote job delegation |
| `DISTRIBUTED_NODE_ID` | container hostname on first start, then stored | Stable node identifier advertised to peers |
| `DISTRIBUTED_NODE_NAME` | `DISTRIBUTED_NODE_ID` | Display name for this worker node |
| `DISTRIBUTED_PUBLIC_URL` | `http://<hostname>:8000` | URL other nodes use to reach this service |
| `DISTRIBUTED_LEADER_URL` | empty | Optional pinned leader URL; when empty, nodes elect a live leader automatically |
| `DISTRIBUTED_PEERS` | empty | Comma-separated peer URLs used when multicast discovery is unavailable |
| `DISTRIBUTED_DISCOVERY_GROUP` | `239.255.42.99` | Multicast group for peer discovery |
| `DISTRIBUTED_DISCOVERY_PORT` | `9988` | UDP port for peer discovery |
| `DISTRIBUTED_HEARTBEAT_SECONDS` | `5` | Peer heartbeat and coordination interval |
| `DISTRIBUTED_PROGRESS_SECONDS` | `1` | Remote worker progress sync interval |
| `DISTRIBUTED_PEER_TTL_SECONDS` | `20` | Seconds before a silent peer is removed |
| `DISTRIBUTED_WORKER_TIMEOUT_SECONDS` | `120` | Seconds an unreachable worker may stay silent before the leader requeues its active job |
| `DISTRIBUTED_MAX_REQUEUES` | `3` | Times a job is requeued after losing its worker before it is marked failed |
| `DISTRIBUTED_STATE_DIR` | `<SOURCE_MOUNT>/.archive-video-av1` | Shared directory holding the cluster queue ledger; must be the same storage on every node |
