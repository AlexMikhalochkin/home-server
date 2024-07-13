#!/bin/bash

check_connection() {
  ncat -z "$TARGET_HOST" "$TARGET_PORT"
  return $?
}

# Function to update firewall rule using doctl
update_firewall() {
  current_ip=$(curl -s https://ipinfo.io/ip)
  doctl compute firewall add-rules "$DIGITAL_OCEAN_FIREWALL_ID" \
    --inbound-rules "protocol:tcp,ports:1883,address:$current_ip"
  echo "$(date): Firewall is updated. New IP: [$current_ip]"
}

while true; do
  if ! check_connection; then
    echo "$(date): Target server not reachable. Updating firewall..."
    update_firewall
  fi
  sleep "$CHECK_INTERVAL_SECONDS"
done
