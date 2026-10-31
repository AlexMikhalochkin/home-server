# home-server
![lint](https://github.com/AlexMikhalochkin/home-server/actions/workflows/lint-docker.yaml/badge.svg)

## Host setup (Ansible)

Two-step Ansible flow so a full OS reinstall can be turned back into a working server with
one GitHub Actions run. See [`ansible/README.md`](ansible/README.md) for full setup/usage.

- **Step 1 — bootstrap (laptop, LAN, one-time per reinstall):** `ansible/bootstrap/playbooks/tailscale.yml`
- **Step 2 — full configure (GitHub Actions "Ansible Configure" workflow):** `ansible/github/playbooks/site.yml`

App state (Home Assistant, Zigbee2MQTT, qBittorrent, Plex, Jellyfin, Grafana, …) is backed up
nightly to S3 with restic; media is not. See [Backups](ansible/README.md#backups).

## Environments

The `chris` environment uses the existing media and smart-home stack in
`docker-compose.yaml`. The `newton` environment is deliberately separate: its
inventory selects `docker-compose.newton.yaml`, which contains only Frigate
(`ghcr.io/blakeblackshear/frigate:0.18.0`) and uses
`/opt/github-deploy/newton/frigate/config` and
`/opt/github-deploy/newton/frigate/storage` on the Newton host. It does not
clone or compose `private-home-server`, and it never selects Chris services.
Frigate's authenticated UI/API is available on port `8971`. Its unauthenticated
internal HTTP port `5000` is not published on the host; RTSP port `8554` and
WebRTC port `8555` remain available.

Select **newton** in either the **Ansible Configure** or **Deploy** workflow.
Before the first Newton deployment, create the encrypted
`ansible/vault/newton-frigate.yml` from its example and populate the four RTSP
URLs for both cameras. Full configure
renders the private
`/opt/github-deploy/newton/frigate/config/config.yaml` from Vault with
restrictive permissions; the plaintext configuration is never committed.
Ansible creates the config and storage directories.

Chris uses a non-login `media` account/group for Plex, Jellyfin, and qBittorrent, with
authenticated Samba access as `alex`. Existing media and application data must be
prepared manually during maintenance before the first full **Ansible Configure**.
Normal Ansible runs maintain accounts, directory-root ACLs and container identities;
they do not back up or recursively migrate existing data. See
[shared media configuration](ansible/README.md#shared-media-identity-on-chris).
The generated media Compose override is part of every subsequent Chris deployment.

The current camera policy disables object and motion detection and defines only
alert retention. It does not retain video in that mode. Continuous recording or
motion-based retention must be configured separately before relying on saved footage.

When moving from the live prototype to Ansible management, stop and remove the
manually created `frigate` container first. Preserve any Frigate state or
recordings you intend to retain by copying them into the new storage/config
paths, then run full configure. The first Compose deployment recreates the
container with the same name and ports.

## Chris service hostnames

Create local DNS **A records pointing to the Chris server's LAN IP**
(shown as `<chris-lan-ip>`) for each hostname below. Do not point these names at Docker
container IPs or the old server. These are HTTP URLs on port 80; HTTPS and public
Internet access are not configured by this change.

| Service | Hostname URL | Existing direct access, retained |
|---|---|---|
| Home Assistant | `http://ha.home.arpa/` | `http://<chris-lan-ip>:8123/` |
| Jellyfin | `http://jellyfin.home.arpa/` | `http://<chris-lan-ip>:8096/` |
| Plex | `http://plex.home.arpa/web/` | `http://<chris-lan-ip>:32400/web/` |
| Grafana | `http://grafana.home.arpa/` | `http://<chris-lan-ip>:3000/` |
| Dozzle | `http://dozzle.home.arpa/` | `http://<chris-lan-ip>:8888/` |
| Zigbee2MQTT | `http://z2m.home.arpa/` | `http://<chris-lan-ip>:8080/` |
| qBittorrent | `http://qbt.home.arpa/` | `http://<chris-lan-ip>:6004/` |
| Traefik | `http://traefik.home.arpa/dashboard/` | `http://<chris-lan-ip>:8082/dashboard/` |

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
curl --resolve grafana.home.arpa:80:<chris-lan-ip> \
  http://grafana.home.arpa/api/health
curl --resolve ha.home.arpa:80:<chris-lan-ip> \
  http://ha.home.arpa/
curl http://<chris-lan-ip>:8123/
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

### Infrastructure regression checks

With Ansible installed in the active Python environment:

```bash
ansible-galaxy collection install -r ansible/requirements.yml
python3 -m unittest discover -s tests -v
```

These checks require Docker Compose but do not start containers or connect to the servers.
They cover host selection, literal shell inputs, branch lookup failures, storage guards,
Compose rendering, and the unchanged camera policy. CI also checks both Ansible playbooks
for both inventories.

CI installs pinned Python dependencies from wheels only. Sonar analyzes `tests/` as test
code, not production code; the test implementations themselves do not require coverage.

The root-only Linux ACL test is skipped on macOS/non-root runs. CI additionally runs it
inside an isolated Python container with the `acl` package, exercising real shared-group
creation, cross-user modification/deletion, outsider denial, and permission restoration.

### Docker Compose Linter (dclint)
```bash
docker run -t --rm --network none -v "${PWD}:/app:ro" \
  zavoloklom/dclint /app/docker-compose.yaml /app/docker-compose.newton.yaml
```

### KICS Security Scanner
```bash
docker run -t --rm --network none \
  -v "${PWD}/docker-compose.yaml:/path/docker-compose.yaml:ro" \
  -v "${PWD}/docker-compose.newton.yaml:/path/docker-compose.newton.yaml:ro" \
  checkmarx/kics scan -p /path -o /tmp/results
```
