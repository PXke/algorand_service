#!/bin/bash
set -eu

host="${CASSANDRA_HOSTS:-cassandra}"
host="${host%%,*}"
port="${CASSANDRA_NATIVE_PORT:-9042}"
deadline="${CASSANDRA_WAIT_SECONDS:-180}"

echo "Waiting for Cassandra at ${host}:${port} (up to ${deadline}s)..."
for ((i = 0; i < deadline; i++)); do
  if python3 - <<PY
import socket
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("${host}", int("${port}")))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
  then
    echo "Cassandra is accepting connections."
    exit 0
  fi
  sleep 1
done

echo "error: Cassandra not ready after ${deadline}s" >&2
exit 1
