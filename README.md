# home-server
![lint](https://github.com/AlexMikhalochkin/home-server/actions/workflows/lint-docker.yaml/badge.svg)

## Host setup (Ansible)

Bootstrap a new machine (Tailscale, and later Docker/Samba/updates) with Ansible.
See [`ansible/README.md`](ansible/README.md) for setup and usage.

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
