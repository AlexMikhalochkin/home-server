# home-server
![lint](https://github.com/AlexMikhalochkin/home-server/actions/workflows/lint-docker.yaml/badge.svg)

## Host setup (Ansible)

Two-step Ansible flow so a full OS reinstall can be turned back into a working server with
one GitHub Actions run. See [`ansible/README.md`](ansible/README.md) for full setup/usage.

- **Step 1 — bootstrap (laptop, LAN, one-time per reinstall):** `ansible/bootstrap/playbooks/tailscale.yml`
- **Step 2 — full configure (GitHub Actions "Ansible Configure" workflow):** `ansible/github/playbooks/site.yml`

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
