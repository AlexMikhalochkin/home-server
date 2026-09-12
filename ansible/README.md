# Ansible

Ansible configures the **host machine** (not just the containers), in two steps, so a full
OS reinstall can be turned back into a working server with (after one-time setup) a single
GitHub Actions run.

| Step | Directory | Run from | Purpose |
|------|-----------|----------|---------|
| 1 | [`bootstrap/`](bootstrap/) | Your laptop, on the **same LAN** as the server, password auth | Join Tailscale, install a GitHub Actions SSH key for the admin user |
| 2 | [`github/`](github/) | **GitHub Actions**, over Tailscale, SSH-key auth | Everything else: OS packages, Docker, Samba, the dedicated deploy user, cloning the repos, and starting the stack |

Why two steps: a freshly reinstalled server only has a LAN IP and a password — Tailscale
and GitHub Actions don't exist on it yet. Step 1 is deliberately the smallest possible
bootstrap (join the tailnet, drop off an SSH key) so it can be run once by hand right after
an OS install. Everything else — which is most of the actual configuration, and the part
you'll want to change/rerun often — lives in step 2 and never touches the server from your
laptop again.

## Concepts (quick reference)

- **Inventory** — list of servers (`inventory/hosts.yml`). No IP addresses in git.
- **Playbook** — checklist of tasks (`playbooks/*.yml`).
- **Role** — reusable tasks (`../roles/`).
- **Vault** — encrypted secrets in git (`../vault/secrets.yml` and the Newton-specific
  `../vault/newton-frigate.yml`).

## Roles

| Role | Runs in | Purpose |
|------|---------|---------|
| `tailscale` | bootstrap | Install and join Tailscale |
| `ssh_ci_access` | bootstrap | Install the GitHub Actions public key for the admin user (`ansible_user`, e.g. `alex`), so step 2 can take over. No-op until `github_ci_ssh_public_key` is set. |
| `common` | github | `apt update/upgrade`, base packages (incl. `acl`, needed for `become_user` tasks), timezone |
| `deploy_user` | github | Create the dedicated `github` deploy user/group (uid/gid picked by the OS — see note below), install its GitHub Actions authorized key |
| `docker` | github | Docker Engine + Compose plugin, adds the deploy user to the `docker` group |
| `storage` | github | **Opt-in** (`storage_disks`). Mount any number of existing disks by UUID/by-id; never reformats a disk that already has a filesystem |
| `samba` | github | Share the configured downloads, TV series, and movies directories (optional) |
| `docker_stack` | github | Select the inventory's Compose files; clone only enabled repositories; render `.env`; install `start.sh`; `docker compose up` |

### Why the deploy user's uid/gid aren't hardcoded

`docker-compose.yaml` runs `mosquitto`/`zigbee2mqtt` as `${DEPLOY_UID:-999}:${DEPLOY_GID:-989}`
so their bind-mounted data (owned by the deploy user) is readable inside the container.
Earlier this was hardcoded to `999:989` on both sides — but a fixed system uid/gid can
already be taken by unrelated accounts on a different OS image (observed in practice:
`systemd-journal`/`_chrony` already own those IDs on a fresh Ubuntu 26.04 install), which
would break the whole reinstall-and-rerun story. The `deploy_user` role now lets the OS pick
a free system uid/gid and feeds the *actual* result into `.env` as `DEPLOY_UID`/`DEPLOY_GID`,
so it works regardless of what the base image already allocated.

## One-time setup

### Newton environment

Newton is selected with the `newton` choice in either GitHub Actions workflow.
Unlike Chris, its inventory uses `hosts-newton.yml` and the Docker stack role
uses only `docker-compose.newton.yaml`; the private service overlay and Chris
storage are not cloned, mounted, or started. The first version exposes Frigate
on ports `8971`, `5000`, `8554`, and `8555` (TCP/UDP), and passes through
`/dev/dri/renderD128` and `/dev/dri/card0`.

Create a dedicated GitHub Actions keypair for Newton:

```bash
ssh-keygen -t ed25519 -f gh_actions_newton -N ""
```

Put its public key in both
`bootstrap/inventory/host_vars/newton/main.yml` and
`github/inventory/host_vars/newton/main.yml`, and put the private key in the
`SSH_PRIVATE_KEY` secret in the Newton GitHub environment. The same key is
installed for the bootstrap admin account and the restricted `github` deploy
account. Do not reuse or replace Chris's key unless that is an intentional
operational decision.

Before running **Ansible Configure** for Newton, create the encrypted
`vault/newton-frigate.yml` from `vault/newton-frigate.yml.example` and populate
the four RTSP URLs from the validated live configuration. Camera credentials
must remain inside that encrypted file. The
`FRIGATE_CONFIG_DIRECTORY` and `FRIGATE_STORAGE_DIRECTORY` paths are rendered
into `.env`, and full configure renders
`/opt/github-deploy/newton/frigate/config/config.yaml` from Vault with
restrictive permissions. Ansible creates both directories without overwriting
existing Frigate state.

When migrating the validated live prototype to Ansible management, stop and
remove its manually created `frigate` container before the first Compose
deployment. Populate `vault/newton-frigate.yml` from the validated camera URLs
before running full configure. Copy any state or recordings you intend to retain
into the new storage path. The Newton Compose file recreates the container with
the same name and ports.

Newton's GitHub environment needs these values:

| Name | Kind | Purpose |
|------|------|---------|
| `TAILSCALE_TARGET_HOST` | var | Newton's Tailscale IP (not its `192.168.1.x` LAN address) |
| `ADMIN_SSH_USER` | var | Sudo-capable bootstrap admin (currently `alex`) |
| `SSH_USER` | var | Restricted deploy user for fast deploys (currently `github`) |
| `TAILSCALE_TAG` | var | Tailscale ACL tag for the runner |
| `SSH_PRIVATE_KEY` | secret | Newton GitHub Actions keypair private half |
| `ANSIBLE_BECOME_PASSWORD` | secret | Admin user's sudo password (Configure only) |
| `ANSIBLE_VAULT_PASSWORD` | secret | `ansible/.vault-pass` (Configure only) |
| `TS_OAUTH_CLIENT_ID` / `TS_AUDIENCE` | secret | Tailscale GitHub Action credentials |

Newton does not need the private-repository deploy-key secrets for the first
version because neither private repository is used.

### 1. Generate the GitHub Actions SSH keypair

One keypair is reused for both steps — to log in as the admin user (`alex`) for the full
`site.yml` configure run, and as the restricted `github` deploy user for the fast redeploy
workflow (`deploy.yaml` / `start.sh`).

```bash
ssh-keygen -t ed25519 -f gh_actions_chris -N ""
```

- Public key → `github_ci_ssh_public_key` in **both**
  `bootstrap/inventory/host_vars/chris/main.yml` and
  `github/inventory/host_vars/chris/main.yml` (not secret, committed).
- Private key → the `SSH_PRIVATE_KEY` secret on the `chris` GitHub environment.

### 2. Generate the two GitHub deploy keys

`home-server-configuration` and `private-home-server` are private repos. Deploy keys are
repo-scoped, so each needs its own keypair, added as a **read-only** deploy key on that repo
(Settings → Deploy keys):

```bash
ssh-keygen -t ed25519 -f deploy_key_home_server_configuration -N ""
ssh-keygen -t ed25519 -f deploy_key_private_home_server -N ""
```

Private key contents go into the vault (below); public keys go on the respective repos.

### 3. Create encrypted secrets

```bash
ansible-vault create ansible/vault/secrets.yml
```

```yaml
tailscale_authkey: "tskey-auth-xxxxxxxx"   # must be reusable/pre-authorized — bootstrap reruns every OS reinstall
samba_password: "your-samba-password"
github_clone_ssh_key_home_server_configuration: |
  -----BEGIN OPENSSH PRIVATE KEY-----
  ...
  -----END OPENSSH PRIVATE KEY-----
github_clone_ssh_key_private_home_server: |
  -----BEGIN OPENSSH PRIVATE KEY-----
  ...
  -----END OPENSSH PRIVATE KEY-----
```

See [`vault/secrets.yml.example`](vault/secrets.yml.example).

### 4. Configure connection (local runs only — CI passes these at runtime)

```bash
cp ansible/bootstrap/inventory/host_vars/chris/local.yml.example \
   ansible/bootstrap/inventory/host_vars/chris/local.yml
# Set ansible_host to the LAN IP

cp ansible/github/inventory/host_vars/chris/local.yml.example \
   ansible/github/inventory/host_vars/chris/local.yml
# Set ansible_host to the Tailscale IP (100.x.x.x)
```

### 5. Optional vault password file

```bash
echo 'your-vault-password' > ansible/.vault-pass
chmod 600 ansible/.vault-pass
```

### 6. Install collections

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

## Step 1 — Bootstrap (from laptop, on the LAN, right after OS install)

```bash
cd ansible/bootstrap
ansible-playbook playbooks/tailscale.yml --ask-pass --ask-become-pass --vault-password-file ../.vault-pass
```

Joins Tailscale and (once `github_ci_ssh_public_key` is set) installs the GitHub Actions
key for the admin user. If the key isn't set yet, this step still succeeds — it only joins
Tailscale, and you can rerun it later once you've generated the keypair.

## Step 2 — Full configure (GitHub Actions, or manually over Tailscale)

Run the **"Ansible Configure"** workflow (`workflow_dispatch`) from GitHub Actions — this is
the "one button" entry point after a reinstall. It needs, on the `chris` (or `jack`/`newton`)
GitHub environment:

| Name | Kind | Value |
|------|------|-------|
| `TAILSCALE_TARGET_HOST` | var | Tailscale IP of the server (e.g. `100.91.97.86`) |
| `ADMIN_SSH_USER` | var | The sudo-capable admin user from step 1 (e.g. `alex`) |
| `TAILSCALE_TAG` | var | Tailscale ACL tag for the GitHub Actions runner |
| `SSH_PRIVATE_KEY` | secret | Private half of the keypair from setup step 1 (shared with `deploy.yaml`) |
| `ANSIBLE_BECOME_PASSWORD` | secret | The admin user's `sudo` password |
| `ANSIBLE_VAULT_PASSWORD` | secret | Contents of `ansible/.vault-pass` |
| `TS_OAUTH_CLIENT_ID` / `TS_AUDIENCE` | secret | Already used by `deploy.yaml` — reused here |

To run it manually instead (from a machine already on the tailnet):

```bash
cd ansible/github
ansible-playbook playbooks/site.yml \
  -e ansible_host=100.x.x.x \
  -e ansible_user=alex \
  --ask-pass --ask-become-pass \
  --vault-password-file ../.vault-pass
```

Run individual parts with tags: `common`, `deploy_user`, `docker`, `storage`, `samba`, `stack`.
For a local Newton run, pass `-i inventory/hosts-newton.yml`; the default inventory remains
the Chris inventory for backwards compatibility.

### What `site.yml` does

1. Updates/upgrades packages, sets the timezone
2. Creates the `github` deploy user (uid/gid picked by the OS) and installs its GitHub
   Actions authorized key
3. Installs Docker Engine + Compose plugin
4. Mounts every disk listed in `storage_disks` (empty by default — see below)
5. Configures Samba shares for downloads, TV series, and movies, if
   `samba_enabled: true`
6. Clones `home-server` (public, HTTPS), plus the enabled private repositories, to
   `/opt/github-deploy/`
7. Renders `/opt/github-deploy/home-server/.env` from inventory vars
8. Installs the inventory-selected `start.sh` (manual break-glass fallback only — see below)
9. Runs the inventory-selected Compose files and profiles (`docker-compose.yaml` plus the
   private overlay for Chris; only `docker-compose.newton.yaml` and `newton` for Newton)
   with `pull` and `up -d`

### Full configure vs. fast deploy — same tasks, different tags

There's a single implementation of "bring the stack up": the `docker_stack` role's tasks,
split into two tags.

| Tag | Runs | Used by |
|-----|------|---------|
| `prepare` | Deploy user SSH keys, directories, `.env`, `start.sh` — one-time/rarely-changing environment setup | `site.yml` full run only |
| `deploy` | Clone/pull the 3 repos, `docker compose pull` + `up` (core stack, then VPN-dependent group), show status | Both the full run **and** the fast `deploy.yaml` workflow |

`deploy.yaml` runs `ansible-playbook site.yml --tags deploy`, connected as the restricted
`github` deploy user — no admin/sudo password or vault password needed, since none of the
`deploy`-tagged tasks touch them (they only read files already installed by a prior
`prepare` pass). This keeps day-to-day redeploys fast and dependency-light while guaranteeing
it's the exact same logic as the full configure, not a second hand-maintained copy.

`start.sh` still exists (for SSHing in directly without CI), but is no longer the primary
path — keep it in mind if you ever change the compose/profile logic, since it duplicates a
small amount of that logic for the manual case.

## Disks and service paths

Disk attachment and service storage are separate settings in the environment file
`github/inventory/host_vars/chris/main.yml`.

With no disks attached, `storage_disks: []` leaves the system disk in use and the stack
still has valid directories under `/opt/github-deploy/storage/`. When a disk is attached,
add it by stable UUID or by-id path:

```yaml
storage_disks:
  - name: media
    source: /dev/disk/by-uuid/<uuid>
    mount_point: /mnt/media
    fstype: ext4
    format: false
```

Then point services at whichever disk should hold their data:

```yaml
service_paths:
  content: /mnt/media/content
  tv_series: /mnt/media/content/tv-series
  movies: /mnt/media/content/movies
  downloads: /mnt/media/downloads
```

Ansible mounts the configured disks, creates all configured service directories, and
renders the corresponding Compose variables into `.env`. qBittorrent uses `downloads`
for completed and incomplete data, while Plex and Jellyfin read the `content`
directory. The three directories are exposed as separate Samba shares. It never
formats an existing filesystem unless `format: true` is explicitly set and the device
has no filesystem.

The private `private-home-server` Compose overlay must use the generated
`QBITTORRENT_ROOT_PATH` variable instead of hardcoded `/media/disk/...` paths.
The root is mounted at `/data` so qBittorrent can move completed downloads into
the media library without copying between Docker mounts.

## Configuration layers

| What | Where | In git? |
|------|--------|---------|
| LAN / Tailscale IP (local runs) | `inventory/host_vars/chris/local.yml` | No |
| GitHub Actions public key, stack paths, profile, timezone, media disk settings | `inventory/host_vars/chris/main.yml` | Yes |
| Tailscale auth key, Samba password, GitHub deploy keys | `vault/secrets.yml` | Yes (encrypted) |
| Admin SSH private key, become password, vault password | GitHub environment secrets | No |

## Ubuntu sudo note

Recent Ubuntu uses `sudo-rs`, which breaks Ansible `become`. This repo sets
`ansible_become_exe: /usr/bin/sudo.ws` in `inventory/group_vars/all.yml` and `ansible.cfg`.

## Directory layout

```
ansible/
├── bootstrap/          # Step 1: Tailscale + CI SSH key (LAN)
├── github/              # Step 2: full configure (Tailscale / CI)
├── roles/
│   ├── tailscale/
│   ├── ssh_ci_access/
│   ├── common/
│   ├── deploy_user/
│   ├── docker/
│   ├── storage/
│   ├── samba/
│   └── docker_stack/
├── requirements.yml     # community.general, ansible.posix
└── vault/
```
