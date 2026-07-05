# Ansible

Ansible configures the **host machine** (not the Docker stack). It is split into two parts:

| Directory | Run from | Purpose |
|-----------|----------|---------|
| [`bootstrap/`](bootstrap/) | Your laptop on the **same LAN** as the server | Install Tailscale (one-time per machine) |
| [`github/`](github/) | **GitHub Actions** (later) | Docker, Samba, users, patching, etc. |

## Concepts (quick reference)

- **Inventory** — list of servers (`inventory/hosts.yml`). No IP addresses in git.
- **Playbook** — checklist of tasks (`playbooks/*.yml`).
- **Role** — reusable tasks (`../roles/`).
- **Vault** — encrypted secrets in git (`../vault/secrets.yml`).

## One-time setup

### 1. Create encrypted secrets

Generate a Tailscale auth key at [Tailscale admin → Keys](https://login.tailscale.com/admin/settings/keys)
(reusable, pre-authorized).

```bash
ansible-vault create ansible/vault/secrets.yml
```

Add:

```yaml
tailscale_authkey: "tskey-auth-xxxxxxxx"
```

Choose a vault password and store it in your password manager.

See [`vault/secrets.yml.example`](vault/secrets.yml.example) for the expected structure.

### 2. Configure connection via IP (bootstrap only)

Ansible connects to the server by **IP address**, not by hostname. You do not need Tailscale
(or any DNS name) for this step — only the machine's address on your local network.

```bash
cp ansible/bootstrap/inventory/host_vars/chris/local.yml.example \
   ansible/bootstrap/inventory/host_vars/chris/local.yml
```

Edit `local.yml`:

- `ansible_host` — LAN IP (e.g. `192.168.1.50`)
- `ansible_user` — SSH user

This file is gitignored.

**Important:** always run the playbook from the `ansible/bootstrap/` directory:

```bash
cd ansible/bootstrap
ansible-playbook playbooks/tailscale.yml ...
```

If `ansible_host` is missing, Ansible falls back to the name `chris` and SSH fails with "Could not resolve hostname chris".

### 3. Optional — vault password file (local only)

To avoid typing the vault password every time:

```bash
echo 'your-vault-password' > ansible/.vault-pass
chmod 600 ansible/.vault-pass
```

`.vault-pass` is gitignored. Use `--vault-password-file ../.vault-pass` when running playbooks from `bootstrap/`.

## Bootstrap Tailscale (from your laptop)

Run from the **`bootstrap/`** directory so Ansible picks up the correct config:

```bash
cd ansible/bootstrap

ansible-playbook playbooks/tailscale.yml \
  --ask-vault-pass \
  --ask-pass \
  --ask-become-pass
```

If SSH/sudo passwords are in `local.yml`, omit `--ask-pass` and `--ask-become-pass`.
If you use `.vault-pass`, swap `--ask-vault-pass` for `--vault-password-file ../.vault-pass`.

**Ubuntu note:** Recent Ubuntu uses `sudo-rs` by default, which breaks Ansible's `become`.
This repo sets `ansible_become_exe: /usr/bin/sudo.ws` in `inventory/group_vars/all.yml` and `ansible.cfg` to use classic sudo.

### What it does

1. Installs Tailscale (official install script)
2. Starts the `tailscaled` service
3. Registers the node on your tailnet (labeled **chris** in Tailscale admin — not used for SSH)
4. Prints the Tailscale IP — add it to GitHub environment variables for future CI runs

### Verify

On the server:

```bash
tailscale status
tailscale ip -4
```

From any machine on your tailnet, reach the server by **Tailscale IP** (same as you will use in GitHub):

```bash
ssh youruser@100.x.x.x
```

## Configuration layers

| What | Where | Used for SSH? | In git? |
|------|--------|---------------|---------|
| LAN IP (bootstrap) | `inventory/host_vars/chris/local.yml` → `ansible_host` | Yes | No (gitignored) |
| Tailscale IP (CI) | GitHub environment variable → `-e ansible_host=...` | Yes | No |
| Label `chris` in Tailscale admin | `inventory/host_vars/chris/main.yml` → `tailscale_hostname` | No | Yes |
| Tailscale auth key | `vault/secrets.yml` | No | Yes (encrypted) |

## GitHub Actions (later)

Playbooks in [`github/`](github/) will run from CI after the runner joins Tailscale.
Pass the server IP at runtime — it is not stored in the repository:

```bash
cd ansible/github
ansible-playbook playbooks/site.yml \
  -e ansible_host=100.x.x.x \
  -e ansible_user=youruser
```

Placeholder playbook only for now; host configuration tasks will be added later.

## Directory layout

```
ansible/
├── README.md                 ← you are here
├── bootstrap/                ← run from laptop (LAN)
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.yml
│   │   ├── group_vars/all.yml
│   │   └── host_vars/chris/
│   │       ├── main.yml
│   │       └── local.yml.example
│   └── playbooks/tailscale.yml
├── github/                   ← run from GitHub Actions (Tailscale IP)
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.yml
│   │   └── host_vars/chris/main.yml
│   └── playbooks/site.yml
├── roles/
│   └── tailscale/
└── vault/
    └── secrets.yml.example
```
