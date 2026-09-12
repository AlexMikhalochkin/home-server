# home-server
![lint](https://github.com/AlexMikhalochkin/home-server/actions/workflows/lint-docker.yaml/badge.svg)

## Host setup (Ansible)

Two-step Ansible flow so a full OS reinstall can be turned back into a working server with
one GitHub Actions run. See [`ansible/README.md`](ansible/README.md) for full setup/usage.

- **Step 1 — bootstrap (laptop, LAN, one-time per reinstall):** `ansible/bootstrap/playbooks/tailscale.yml`
- **Step 2 — full configure (GitHub Actions "Ansible Configure" workflow):** `ansible/github/playbooks/site.yml`

## Environments

The `chris` environment uses the existing media and smart-home stack in
`docker-compose.yaml`. The `newton` environment is deliberately separate: its
inventory selects `docker-compose.newton.yaml`, which contains only Frigate
(`ghcr.io/blakeblackshear/frigate:0.18.0`) and uses
`/opt/github-deploy/newton/frigate/config` and
`/opt/github-deploy/newton/frigate/storage` on the Newton host. It does not
clone or compose `private-home-server`, and it never selects Chris services.

Select **newton** in either the **Ansible Configure** or **Deploy** workflow.
Before the first Newton deployment, create the encrypted
`ansible/vault/newton-frigate.yml` from its example and populate the four RTSP
URLs for the cameras at `192.168.1.246` and `192.168.1.247`. Full configure
renders the private
`/opt/github-deploy/newton/frigate/config/config.yaml` from Vault with
restrictive permissions; the plaintext configuration is never committed.
Ansible creates the config and storage directories.

When moving from the live prototype to Ansible management, stop and remove the
manually created `frigate` container first. Preserve any Frigate state or
recordings you intend to retain by copying them into the new storage/config
paths, then run full configure. The first Compose deployment recreates the
container with the same name and ports.

## Local Verification

Verify `docker-compose.yaml` locally using:

### Docker Compose Linter (dclint)
```bash
docker run -t --rm -v ${PWD}:/app zavoloklom/dclint /app/docker-compose.yaml
```

### KICS Security Scanner
```bash
docker run -t --rm -v "./docker-compose.yaml":/path/docker-compose.yaml checkmarx/kics scan -p /path -o "/path/"
```
