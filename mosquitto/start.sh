#!/bin/ash
set -e

CONFIG_FILE="/mosquitto/config/mosquitto.conf"
ADDRESS_ENTRY="address $BRIDGE_ADDRESS"

if ! grep -q "$ADDRESS_ENTRY" "$CONFIG_FILE"; then
  echo "$ADDRESS_ENTRY" >> "$CONFIG_FILE"
fi

exec "$@"
