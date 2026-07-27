# home-server
![lint](https://github.com/AlexMikhalochkin/home-server/actions/workflows/lint-docker.yaml/badge.svg)

## DNS Server Setup

This stack includes a local DNS server (dnsmasq) for resolving custom domain names within your home lab.

### Initial Configuration

1. Copy the dnsmasq configuration template:
   ```bash
   cp dnsmasq/dnsmasq.conf.example dnsmasq/dnsmasq.conf
   ```

2. Edit `dnsmasq/dnsmasq.conf` and update the IP addresses to match your `HOST_IP` from `.env` file.

3. The dnsmasq web UI is available at `http://<HOST_IP>:5380` (credentials from `.env` file).

### Adding Custom DNS Entries

To add more local DNS names, edit `dnsmasq/dnsmasq.conf` and add lines in the format:
```
address=/hostname.home/<your-host-ip>
```

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
