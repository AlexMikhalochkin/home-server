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

## Chris service hostnames

Create local DNS **A records pointing to the Chris server's LAN IP** (currently
`192.168.100.116`) for each hostname below. Do not point these names at Docker
container IPs or the old server. These are HTTP URLs on port 80; HTTPS and public
Internet access are not configured by this change.

| Service | Hostname URL | Existing direct access, retained |
|---|---|---|
| Home Assistant | `http://ha.home.arpa/` | `http://192.168.100.116:8123/` |
| Jellyfin | `http://jellyfin.home.arpa/` | `http://192.168.100.116:8096/` |
| Plex | `http://plex.home.arpa/web/` | `http://192.168.100.116:32400/web/` |
| Grafana | `http://grafana.home.arpa/` | `http://192.168.100.116:3000/` |
| Dozzle | `http://dozzle.home.arpa/` | `http://192.168.100.116:8888/` |
| Zigbee2MQTT | `http://z2m.home.arpa/` | `http://192.168.100.116:8080/` |
| qBittorrent | `http://qbt.home.arpa/` | `http://192.168.100.116:6004/` |
| Traefik | `http://traefik.home.arpa/dashboard/` | `http://192.168.100.116:8082/dashboard/` |

All existing host port mappings, Docker networks, and legacy `grafana.home` /
`traefik.home` routing rules remain unchanged. Legacy DNS records still need to
resolve to the current server to work. The dashboard's `/dashboard/` path
requires its trailing slash.

Routes are maintained in `traefik/dynamic/home.yml`, mounted as a watched
directory. Normal services use Compose DNS names. Home Assistant is reached
through `openvpn:8123` because it shares that container's network namespace;
Plex uses `host.docker.internal:32400` because it uses host networking.
qBittorrent remains defined in the private Compose repository but is routed
here through its existing shared Compose network. An optional or stopped
service's hostname will return a gateway error; defining a route does not
start the service.

Mosquitto's MQTT listener (`1883/tcp`), the VPN tunnel, torrent peer ports, and
other non-HTTP protocols are not HTTP hostname routes. Their direct access is
unchanged. DNS names alone cannot multiplex plain MQTT on HTTP port 80.

### Home Assistant prerequisite and deployment

Home Assistant must trust the reverse proxy or it rejects forwarded requests.
On Chris (Home Assistant 2026.9), HTTP settings have already migrated out of
YAML. Use **Settings > System > Network > HTTP server**: enable **Trust
X-Forwarded-For** and add `172.18.0.0/16` to **Trusted proxies**, retaining port
`8123` and the other existing settings. Save, allow the restart, then confirm
the working settings within the five-minute trial window so HA does not revert
them. Adding an `http:` block to YAML after migration does not update these
runtime settings.

This CIDR is the Chris `home-server_default` Docker bridge, not the home LAN.
Confirm it with `docker network inspect home-server_default` before applying
elsewhere or after recreating the network. Do not trust every source address.
The HTTP settings are persisted by HA under its runtime `/config/.storage`,
outside the configuration repository. Include that state in backups and
reapply the setting after a fresh HA installation. Its original IP-and-port
URL remains usable; do not force a canonical-host redirect.

Use the **Deploy** workflow, environment **chris**, with the branch containing
these Traefik changes once they are committed and pushed. No private Compose
or HA YAML change is needed. Uncommitted live repository edits can be
overwritten by deployment.

For a controlled manual application, back up the affected files first, validate
the merged Compose configuration, and recreate only Traefik with the usual
Compose files/environment and `up -d --no-deps traefik`. Its new dynamic-config
mount requires recreation on the first deployment; later route-only edits are
watched automatically. No application port or router port-forwarding change
is needed.

### Before changing DNS

Test through Traefik with a temporary host mapping on the request:

```bash
curl --resolve grafana.home.arpa:80:192.168.100.116 \
  http://grafana.home.arpa/api/health
curl --resolve ha.home.arpa:80:192.168.100.116 \
  http://ha.home.arpa/
curl http://192.168.100.116:8123/
```

Verify every hostname and the old URLs, including Home Assistant and
Zigbee2MQTT WebSocket connections. Configure router DNS only after routing
works. Clients must use a resolver that knows these local names; encrypted DNS,
VPN DNS, or stale caches can bypass the router's records.

### Access boundary

This adds no authentication or TLS. Existing application authentication remains
in effect. Prometheus and node-exporter have no hostname routes or published
host ports; their existing internal access and scraping remain unchanged.
Existing anonymous dashboards stay anonymous. Use these routes only on trusted networks.
Do not forward port 80 from the Internet as part of this transition. Existing
router forwarding and firewall settings are not modified by this configuration.

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
