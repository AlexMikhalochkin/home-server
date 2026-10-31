# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Infrastructure-as-code for a personal home server running a Docker-based smart home and media stack. Everything from bare OS to a running server is automated via Ansible and GitHub Actions.

## Local Verification Commands

```bash
# Infrastructure regression checks (Ansible, its collections, and Compose required)
python3 -m unittest discover -s tests -v

# Lint both Compose files (0 warnings allowed, same check as CI)
docker run -t --rm --network none -v "${PWD}:/app:ro" \
  zavoloklom/dclint /app/docker-compose.yaml /app/docker-compose.newton.yaml

# KICS security scan on both Compose files
docker run -t --rm --network none \
  -v "${PWD}/docker-compose.yaml:/path/docker-compose.yaml:ro" \
  -v "${PWD}/docker-compose.newton.yaml:/path/docker-compose.newton.yaml:ro" \
  checkmarx/kics scan -p /path -o /tmp/results
```

Infrastructure regression tests live in `tests/`. CI runs them and checks Ansible syntax
for both hosts; runtime validation still requires a manual deploy.

## Architecture

### Three-Repo Composition

The Docker stack is split across three repos merged at runtime with `docker compose -f ... -f ...`:
- `home-server` (this repo, public) — stack definition and infrastructure
- `home-server-configuration` (private) — config files for services
- `private-home-server` (private) — additional private services (e.g. qbittorrent)

### Deployment Flow (Two Steps)

**Step 1 — Bootstrap** (run once from laptop on LAN after OS reinstall):
```bash
cd ansible/bootstrap
ansible-playbook playbooks/tailscale.yml \
  --ask-pass --ask-become-pass \
  --vault-password-file ../.vault-pass
```
Installs Tailscale and the GitHub Actions SSH public key on the server.

**Step 2 — Full Configure** (`ansible-configure` GitHub Actions workflow, manual `workflow_dispatch`):
Runs `ansible/github/playbooks/site.yml` with no tag filter via Tailscale as the admin user (needs vault + become password).

**Fast Redeploy** (`deploy` GitHub Actions workflow):
Runs the same playbook with `--tags deploy` as the restricted `github` deploy user — no vault or become password needed. This pulls latest code and restarts the stack only.

### Ansible Role Tags

The `docker_stack` role uses two tags to enable both flows from the same playbook:

| Tag | What runs |
|---|---|
| `prepare` | SSH keys, directories, `.env`, `start.sh` — full configure only |
| `deploy` | `git clone/pull` 3 repos, `docker compose pull` + `up` — both full and fast deploy |

Never add deploy-only logic to `prepare`-tagged tasks (and vice versa).

### Docker Compose Profiles

Services are grouped by profile to control which run on a given host:

| Profile | Services |
|---|---|
| `chris` | dozzle, grafana, home-assistant, jellyfin, matterbridge, mosquitto, node-exporter, openvpn, plex, prometheus, traefik |
| `chris-zigbee` | zigbee2mqtt (only when USB dongle physically present) |
| `newton` | Frigate-only Newton environment (selected through its dedicated Compose file) |
| `local` | mosquitto variants for other hosts |

### Key Design Decisions

- **`DEPLOY_UID`/`DEPLOY_GID` are not hardcoded** — the OS assigns a free system uid/gid at deploy user creation; this is fed into `.env`. Necessary because Ubuntu changes system uid allocation between versions.
- **Storage is declarative and multi-disk** (`storage` role) — each optional disk uses a stable UUID/by-id source and an explicit mount point; services consume named paths from the environment-specific host vars. Disks are never reformatted unless an entry explicitly sets `format: true` and no filesystem exists.
- **Grafana anonymous admin access** — intentional, LAN-only.
- **Backups cover app state only** (`backup` role, full configure only) — restic → S3
  (`momomo-home-servers-backups`, eu-central-1) nightly at 04:00 with a brief stop of
  state-owning containers. Media, caches and logs are never backed up. Secrets live in
  `ansible/vault/secrets.yml`; see `ansible/README.md` "Backups".
- **Shared media identity** — Chris provisions a non-login `media` account/group with
  OS-assigned IDs. A last-applied, Ansible-rendered `media-compose.yaml` aligns Plex,
  Jellyfin, and private qBittorrent; Samba remains authenticated as `alex`. Existing data
  is prepared manually, with backups, outside this repository. Ansible maintains roots
  and known configuration-file ACLs only; fast deploy must not create accounts or migrate
  permissions. Private configuration updates must not discard application-written settings.
- **`openvpn` network namespace sharing** — `home-assistant` uses `network_mode: "service:openvpn"`; the VPN is always on and part of the `chris` profile, so a VPN failure fails the deploy.

### Secrets

- `ansible/vault/secrets.yml` — ansible-vault encrypted, committed. Contains Tailscale auth key, Samba password, deploy SSH keys for private repos.
- GitHub environment secrets — `SSH_PRIVATE_KEY`, `ANSIBLE_BECOME_PASSWORD`, `ANSIBLE_VAULT_PASSWORD`, `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`.
- `ansible/github/inventory/local.yml` — gitignored, used for local manual runs (server IP).

### Observability

- Traefik reverse proxy on port 80, dashboard on port 8082. HTTP service routes are in
  `traefik/dynamic/home.yml` using `*.home.arpa`; legacy `grafana.home` and `traefik.home`
  rules and all direct IP/port access are retained. See README for DNS and HA proxy prerequisites.
- Prometheus scrapes `node-exporter:9100` and `traefik:8080` every 15s.
- Dozzle at port 8888 for real-time container logs.
