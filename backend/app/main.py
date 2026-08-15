"""Falcon app entrypoint shim."""

from __future__ import annotations

from app.falcon_main import app

if __name__ == "__main__":
    # Development fallback; production should use gunicorn with gthread workers.
    from wsgiref.simple_server import make_server

    from app.core.config import settings

    host = settings.app_host
    port = settings.app_port
    with make_server(host, port, app) as server:
        server.serve_forever()
