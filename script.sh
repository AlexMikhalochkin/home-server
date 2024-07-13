#!/bin/bash

check_connection() {
  ncat -z "$TARGET_HOST" "$TARGET_PORT"
  return $?
}

update_firewall() {
  current_ip=$(curl -s https://ipinfo.io/ip)
  /app/doctl compute firewall add-rules "$DIGITAL_OCEAN_FIREWALL_ID" \
    --inbound-rules "protocol:tcp,ports:1883,address:$current_ip"
  if [ $? -eq 0 ]; then
    echo "$(date): Firewall is updated. New IP: [$current_ip]"
  else
    echo "$(date): Failed to update the firewall."
  fi
}

# TODO add validation of environment variables

while true; do
  if ! check_connection; then
    echo "$(date): Target server not reachable. Updating firewall..."
    update_firewall
  fi
  sleep "$CHECK_INTERVAL_SECONDS"
done
