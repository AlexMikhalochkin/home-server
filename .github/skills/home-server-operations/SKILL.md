---
name: home-server-operations
description: Safely diagnose and recover this Docker/Ansible home-server stack, including live SSH inspection, container logs, networking, permissions, and backups.
---

# Home-server operations

Use this skill for live diagnosis, recovery, or operational changes to the Docker-based home-server stack.

## Operational modes

Classify the requested work before using a mutating command:

- **inspect** — read-only diagnosis and evidence collection;
- **prototype** — one explicitly approved live change, with backup and rollback;
- **apply** — synchronize a validated prototype into the correct source repository;
- **rollback** — restore the backed-up live state and verify the affected service;
- **deploy preparation** — validate the source diff and describe the exact workflow/branch to run.

Stay in inspect mode when the user asks for a plan or investigation. Do not commit, push,
restart, edit, or deploy unless that transition is explicitly requested.

## Safety

- Identify the target host, deployment directory, Compose files, environment file, and active profile before acting.
- Use SSH only with credentials supplied by the user or environment. Never store or print passwords, private keys, or decrypted secrets.
- Prefer configured key-based SSH or Ansible inventory credentials. If the configured
  Tailscale path is unavailable, use a user-provided LAN address only for the current
  operation; do not rewrite inventory files or persist the fallback address.
- Start with read-only inspection. Before changing a remote file, create a timestamped copy.
- Prefer the smallest service-level change. Do not recreate the whole stack or delete data without explicit approval.
- Treat Git changes, runtime state, secrets, and backups as separate concerns.
- Never include unrelated modified or untracked files in a proposed source change, backup,
  commit, or deployment.

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

For event-driven services, trace the complete path rather than inspecting only the final
container:

1. capture the source event/topic, payload, timestamp, QoS, and retained flag;
2. capture the application automation or handler invocation;
3. capture the downstream command/request;
4. capture the resulting device/service state and any retry, timeout, or duplicate event.

Use temporary listeners and probes for this investigation. Stop them after the test, and
redact credentials, tokens, network keys, and sensitive application payloads from output.
For MQTT specifically, check retained messages and duplicate/replayed status updates before
adding debounce logic or changing command semantics. For Zigbee or other mesh protocols,
compare link quality, route/retry failures, coordinator placement, interference, and
powered routers separately from the application automation.

## Live experiment protocol

When a prototype is approved:

1. Identify the live file or state and its source-of-truth repository.
2. Record the current service status, relevant logs, and configuration revision.
3. Create a timestamped backup at the exact path being changed.
4. Make one focused change only.
5. Validate syntax or configuration before restarting anything.
6. Restart or reload only the affected service.
7. Ask the user to perform the real-world test when physical devices or UI interaction are involved.
8. Verify container health, endpoint behavior, logs, and the observed user-facing result.
9. Record whether the change exists only live or has been synchronized into Git.

If the experiment fails, stop and rollback the specific backup instead of layering more
changes on top. Do not leave a successful live prototype undocumented.

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

- This deployment is composed from three repositories:
 - `home-server` — public Compose and infrastructure;
 - `home-server-configuration` — service configuration bind-mounted into containers;
 - `private-home-server` — private Compose additions.
- Before editing a live bind-mounted file, map it to the repository that owns it. The
 `docker_stack` role can force-update checkouts, so uncommitted server changes may be
 discarded by Ansible.
- Git stores reproducible Compose, Ansible, and static service configuration.
- Runtime directories and named volumes store application state such as Home Assistant `.storage`, Plex/Jellyfin databases, Grafana data, and qBittorrent state.
- Secrets must be encrypted and handled separately from ordinary configuration.
- Caches, logs, downloads, and replaceable media do not automatically require backup.
- A backup stored on the same physical server is not an independent backup. Prefer encrypted Restic/Borg backups to remote S3-compatible storage or separate hardware.

When synchronizing a successful live prototype:

1. copy the validated diff into the owning local repository;
2. preserve unrelated working-tree changes;
3. run the repository's existing syntax/lint checks;
4. report the exact files and repository changed;
5. do not commit or push unless requested;
6. tell the user which GitHub Actions workflow, environment, and branch to deploy;
7. after deployment, repeat the user-facing test and verify the live revision.

## Handoff report

Every completed operation should state:

- target host and access path used;
- root cause or evidence found;
- live changes and their backup/rollback paths;
- source repository and files changed;
- unrelated changes deliberately preserved;
- service restarted or reloaded;
- validation result and remaining manual action.
