#!/bin/bash
# Network isolation entrypoint for NanoClaw containers.
# Reads /workspace/network-policy.json and sets up iptables rules
# before handing off to the original entrypoint.
set -e

POLICY_FILE="/workspace/network-policy.json"

if [ -f "$POLICY_FILE" ]; then
  # Resolve host gateway IP
  HOST_IP=$(getent hosts host.docker.internal | awk '{print $1}')

  if [ -z "$HOST_IP" ]; then
    echo "[network] WARNING: Could not resolve host.docker.internal, skipping network policy" >&2
  else
    BLOCK_PRIVATE=$(jq -r '.blockPrivateRanges // false' "$POLICY_FILE")

    if [ "$BLOCK_PRIVATE" = "true" ]; then
      # Allow loopback
      iptables -A OUTPUT -o lo -j ACCEPT

      # Allow established connections
      iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

      # Allow DNS (UDP+TCP port 53)
      iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
      iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

      # Allow whitelisted host ports
      for PORT in $(jq -r '.allowedHostPorts[]' "$POLICY_FILE"); do
        iptables -A OUTPUT -d "$HOST_IP" -p tcp --dport "$PORT" -j ACCEPT
        echo "[network] Allowed: $HOST_IP:$PORT" >&2
      done

      # Block RFC1918 private ranges (except already-allowed host ports above)
      iptables -A OUTPUT -d 10.0.0.0/8 -j DROP
      iptables -A OUTPUT -d 172.16.0.0/12 -j DROP
      iptables -A OUTPUT -d 192.168.0.0/16 -j DROP
      # Also block link-local
      iptables -A OUTPUT -d 169.254.0.0/16 -j DROP

      echo "[network] Private ranges blocked, internet allowed" >&2
    else
      echo "[network] blockPrivateRanges=false, no restrictions applied" >&2
    fi
  fi
else
  echo "[network] No network policy found at $POLICY_FILE, no restrictions applied" >&2
fi

# Drop privileges and hand off to original entrypoint.
# NANOCLAW_RUN_AS is set by the host when bind-mount ownership requires a specific UID:GID.
# Falls back to the 'node' user (uid 1000) which is the default non-root user in the image.
# gosu resets HOME based on /etc/passwd, but custom UIDs (e.g. macOS 501) aren't listed there,
# causing HOME="/". We force HOME=/home/node via env prefix so the SDK finds .claude/ correctly.
RUN_AS="${NANOCLAW_RUN_AS:-node}"
exec gosu "$RUN_AS" env HOME=/home/node /app/entrypoint.sh
