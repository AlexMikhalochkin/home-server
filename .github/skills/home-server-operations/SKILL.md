---
name: home-server-operations
description: Safely diagnose and recover this Docker/Ansible home-server stack, including live SSH inspection, container logs, networking, permissions, and backups.
---

# Home-server operations

Use this skill for live diagnosis, recovery, or operational changes to the Docker-based home-server stack.

## Safety

- Identify the target host, deployment directory, Compose files, environment file, and active profile before acting.
- Use SSH only with credentials supplied by the user or environment. Never store or print passwords, private keys, or decrypted secrets.
- Start with read-only inspection. Before changing a remote file, create a timestamped copy.
- Prefer the smallest service-level change. Do not recreate the whole stack or delete data without explicit approval.
- Treat Git changes, runtime state, secrets, and backups as separate concerns.

## Inspect first

Check the affected service and its dependencies:

```bash
docker ps -a
docker logs --since 15m SERVICE
docker inspect SERVICE --format '{{json .Mounts}}'
docker inspect SERVICE --format 'network={{.HostConfig.NetworkMode}} ports={{json .HostConfig.PortBindings}}'
docker compose ... config --quiet
```

When the host is remote, run equivalent commands over SSH. Inspect host-side paths, ownership, free space, and the rendered Compose environment when permissions or mounts are involved.

Classify the evidence before changing anything:

- configuration or missing include;
- runtime state or database;
- permissions or ownership;
- network mode, port, DNS, or dependency;
- authentication or secret;
- storage, capacity, or backup.

## Apply and verify

1. Preserve the live Compose/configuration file or state being changed.
2. Make one focused change.
3. Recreate only the affected service.
4. Verify all of:
   - container status is `Up`;
   - expected port or HTTP endpoint responds;
   - mounts, network mode, and ports match the intended design;
   - recent logs contain no new startup or permission errors.
5. Report the evidence, root cause, change, persistence status, and any remaining manual action.

Bind-mounted files can be reported as `Resource busy` during application restores. Temporarily remove the individual file mounts, keep the parent configuration directory mounted, recreate the service, perform the restore, and only then decide how restored files should be synchronized back to Git.

## Configuration and backup boundaries

- Git stores reproducible Compose, Ansible, and static service configuration.
- Runtime directories and named volumes store application state such as Home Assistant `.storage`, Plex/Jellyfin databases, Grafana data, and qBittorrent state.
- Secrets must be encrypted and handled separately from ordinary configuration.
- Caches, logs, downloads, and replaceable media do not automatically require backup.
- A backup stored on the same physical server is not an independent backup. Prefer encrypted Restic/Borg backups to remote S3-compatible storage or separate hardware.
