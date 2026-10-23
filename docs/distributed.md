# Distributed Processing

Several trusted LAN devices can run the container and share one AV1 queue. Jobs can be queued from any node; an elected leader hands pending work to idle peers, and every node shows the same queue with its worker assignments. Cluster state is shown on the Cluster page and at `/api/cluster/status`.

## Setup

1. Run the container on every node with the same library mounted at the same path (`/videos` by default). Host networking (`--network host`) is the most reliable option for multicast discovery.
2. On each node's Settings page, turn on distributed mode and set the public URL other nodes reach it at. Without multicast, list the other nodes under Peers.
3. Restart each node from the Settings page.

```bash
docker run -d \
  --name convert-service \
  --restart unless-stopped \
  --network host \
  -v /mnt/media:/videos \
  -v archive-video-av1-data:/app/data \
  ghcr.io/fabianwimberger/archive-video-av1:latest
```

Leave the leader URL empty to elect a leader automatically; set it only to pin one coordinator. Node IDs are taken from the hostname on first start and stored, so they stay stable when a container is recreated.

To build nodes from source, put your node-specific ports or networking in a `docker-compose.cluster.yml` and use `make node-build`, `make node-up`, `make node-recreate` and `make node-down`.

## Requirements

- All nodes run the same version.
- The shared filesystem supports cross-host POSIX byte-range locks and hard links. NFSv4 does (keep the default `local_lock=none`), and a node on the file server itself shares locks with NFS clients. SMB/CIFS mounts are not supported.
- `DISTRIBUTED_STATE_DIR` (inside the library by default) resolves to the same writable directory on every node.
- Every node reaches every other node through its public URL, and the network allows UDP multicast on port `9988` or every node lists the others as peers.
- Converted files take the source file's mode and owner. Where a mount maps users (NFS `root_squash`, SMB `uid=`/`gid=`), the owner change may be refused; the job still succeeds.
- The service has no authentication, so only enable distributed mode on a trusted network.

## Failure handling

- Only the leader changes the queue and mirrors it to a ledger in `DISTRIBUTED_STATE_DIR`. A new leader loads the queue from that ledger, so a node that was offline never resumes jobs deleted or finished elsewhere. Queue changes are refused with `503` for a few seconds while a new leader takes over.
- A node that stops cleanly hands its running job back right away. A node that goes silent has its job requeued after `DISTRIBUTED_WORKER_TIMEOUT_SECONDS`, and stops encoding if it comes back and sees the job reassigned. A job that loses its worker `DISTRIBUTED_MAX_REQUEUES` times is marked failed.
- While an output is written or deleted, a hidden `.<name>_conv.mkv.lock` file next to it guards it against other nodes.
- When upgrading from a version without the queue ledger, upgrade all nodes together and start the previous leader first, or let the queue run empty before upgrading.

## Environment variables

Distributed mode, node name, public URL, leader URL and peers are set on the Settings page. The variables `DISTRIBUTED_ENABLED`, `DISTRIBUTED_NODE_NAME`, `DISTRIBUTED_PUBLIC_URL`, `DISTRIBUTED_LEADER_URL` and `DISTRIBUTED_PEERS` override them and lock the field. The rest are environment-only:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTRIBUTED_NODE_ID` | stored | Overrides the stored node ID |
| `DISTRIBUTED_DISCOVERY_GROUP` | `239.255.42.99` | Multicast group for peer discovery |
| `DISTRIBUTED_DISCOVERY_PORT` | `9988` | UDP port for peer discovery |
| `DISTRIBUTED_HEARTBEAT_SECONDS` | `5` | Peer heartbeat and coordination interval |
| `DISTRIBUTED_PROGRESS_SECONDS` | `1` | Remote worker progress sync interval |
| `DISTRIBUTED_PEER_TTL_SECONDS` | `20` | Seconds before a silent peer is removed |
| `DISTRIBUTED_WORKER_TIMEOUT_SECONDS` | `120` | Seconds a silent worker keeps its job before it is requeued |
| `DISTRIBUTED_MAX_REQUEUES` | `3` | Requeues after a lost worker before a job is marked failed |
| `DISTRIBUTED_STATE_DIR` | `<SOURCE_MOUNT>/.archive-video-av1` | Shared directory for the queue ledger |
