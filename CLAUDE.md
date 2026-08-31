# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Infrastructure-as-code for a personal home server running a Docker-based smart home and media stack. Everything from bare OS to a running server is automated via Ansible and GitHub Actions.

## Local Verification Commands

```bash
# Lint docker-compose.yaml (0 warnings allowed, same check as CI)
docker run -t --rm -v ${PWD}:/app zavoloklom/dclint /app/docker-compose.yaml

# KICS security scan on docker-compose.yaml
docker run -t --rm -v "./docker-compose.yaml":/path/docker-compose.yaml \
  checkmarx/kics scan -p /path -o "/path/"
```

There are no unit tests. Validation is entirely through the linters above and manual deploy.

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
| `chris` | dozzle, grafana, matterbridge, mosquitto, node-exporter, plex, prometheus, traefik |
| `chris-vpn` | openvpn, home-assistant, jellyfin |
| `chris-zigbee` | zigbee2mqtt (only when USB dongle physically present) |
| `newton`, `local` | mosquitto variants for other hosts |

The `chris-vpn` profile is started in a separate best-effort `docker compose up` pass so VPN failures cannot abort the rest of the stack.

### Key Design Decisions

- **`DEPLOY_UID`/`DEPLOY_GID` are not hardcoded** — the OS assigns a free system uid/gid at deploy user creation; this is fed into `.env`. Necessary because Ubuntu changes system uid allocation between versions.
- **Media disk uses UUID** (`storage` role) — survives port/enclosure changes. Will never reformat unless `media_disk_format: true` is explicitly set.
- **Grafana anonymous admin access** — intentional, LAN-only.
- **`openvpn` network namespace sharing** — `home-assistant` and `jellyfin` use `network_mode: "service:openvpn"` so their traffic routes through the VPN.

### Secrets

- `ansible/vault/secrets.yml` — ansible-vault encrypted, committed. Contains Tailscale auth key, Samba password, deploy SSH keys for private repos.
- GitHub environment secrets — `SSH_PRIVATE_KEY`, `ANSIBLE_BECOME_PASSWORD`, `ANSIBLE_VAULT_PASSWORD`, `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`.
- `ansible/github/inventory/local.yml` — gitignored, used for local manual runs (server IP).

### Observability

- Traefik reverse proxy on port 80, dashboard on port 8082. Routes: `grafana.home`, `traefik.home`.
- Prometheus scrapes `node-exporter:9100` and `traefik:8080` every 15s.
- Dozzle at port 8888 for real-time container logs.
