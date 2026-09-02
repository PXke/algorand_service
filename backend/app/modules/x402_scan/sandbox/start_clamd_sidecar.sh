#!/usr/bin/env sh
# Starts (or restarts) the long-lived clamd sidecar that keeps ClamAV's
# signature DB warm in memory, so per-request scans talk to it over a Unix
# domain socket instead of paying clamscan's ~12s full-DB reload on every
# single invocation (see checks/clamav.py's docstring for the measured
# numbers and the fallback path).
#
# Run this ONCE per image build/rebuild (the image bakes in a fresh
# signature DB at build time via freshclam -- see ../Dockerfile -- so a
# daily DB-refresh rebuild is also the sidecar's restart cadence). It does
# NOT need restarting on every deploy of backend/ code, only when
# algorand-x402-scan:v0 itself is rebuilt.
#
# Usage:
#   ./start_clamd_sidecar.sh                 # uses defaults below
#   X402_CLAMD_SOCKET_DIR=/custom/path ./start_clamd_sidecar.sh
#
# To check it's up:      docker logs x402-clamd
# To restart after a rebuild:  ./start_clamd_sidecar.sh   (removes + recreates)
# To stop it entirely:   docker rm -f x402-clamd
#
# The per-request ephemeral containers (services/sandbox_runner.py) must
# bind-mount this SAME host directory read-write at the SAME container path
# alongside their existing `-v <file>:/scan/input:ro` mount -- see
# checks/clamav.py's CLAMD_SOCKET_DIR constant and this repo's accompanying
# report for the exact `docker run` line sandbox_runner.py still needs.

set -eu

IMAGE="algorand-x402-scan:v0"
CONTAINER_NAME="x402-clamd"
SOCKET_DIR="${X402_CLAMD_SOCKET_DIR:-/var/run/x402-clamd}"
CONF_PATH_IN_IMAGE="/etc/clamav/clamd-sidecar.conf"

mkdir -p "$SOCKET_DIR"
# World-writable: this directory holds only the clamd Unix socket + its log/
# pid files, is bind-mounted solely into this sidecar and the per-request
# scan containers (never published on the host network), and both sides run
# as the same non-root uid (10001, "scanner") -- see clamd.conf's own
# comment on LocalSocketMode for the same reasoning.
chmod 777 "$SOCKET_DIR"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "start_clamd_sidecar.sh: removing existing $CONTAINER_NAME container" >&2
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

# 1536m (vs. the per-request scan container's 512m) is deliberate, not a
# typo: a fully-loaded ClamAV DB (main.cvd + daily.cvd + bytecode) measured
# at ~944MiB resident once clamd finishes loading -- 512m OOM-killed this
# container outright (never got past "Reading databases..." in the log)
# before this was raised. This container is long-lived and singular (one
# sidecar, not one per request), so the extra memory budget is cheap.
docker run -d \
    --name "$CONTAINER_NAME" \
    --network none \
    --user 10001:10001 \
    --read-only \
    --tmpfs /tmp:size=64m \
    --memory 1536m \
    --memory-swap 1536m \
    --pids-limit 64 \
    --cpus 1 \
    --security-opt no-new-privileges \
    --cap-drop ALL \
    --restart unless-stopped \
    -v "$SOCKET_DIR:/run/clamav" \
    --entrypoint clamd \
    "$IMAGE" \
    -c "$CONF_PATH_IN_IMAGE"

echo "start_clamd_sidecar.sh: started $CONTAINER_NAME, socket dir: $SOCKET_DIR" >&2
