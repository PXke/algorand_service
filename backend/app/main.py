from __future__ import annotations

from robyn import Robyn

from app.core.config import settings
from app.core.cors import register_cors
from app.core.health import run_readiness_checks
from app.core.observability import init_bugsnag
from app.modules.admin.api.routes import register_admin_routes
from app.modules.auth.api.routes import register_auth_routes
from app.modules.ingest.api.routes import register_ingest_routes
from app.modules.media.api.routes import register_media_routes
from app.modules.metrics.api.routes import register_metrics_routes
from app.modules.news.api.routes import register_news_routes
from app.modules.placements.api.routes import register_placement_routes
from app.modules.registry.api.routes import register_registry_routes
from app.modules.search.api.routes import register_search_routes
from app.modules.seo.api.routes import register_seo_routes
from app.modules.suggestions.api.routes import register_suggestions_routes

app = Robyn(__file__)
init_bugsnag(release_stage=settings.app_env or "prod")
register_cors(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    checks = run_readiness_checks()
    ok = all(check.ok for check in checks if check.name in {"redis", "cassandra"})
    return {
        "status": "ok" if ok else "degraded",
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
    }


register_auth_routes(app)
register_media_routes(app)
register_metrics_routes(app)
register_news_routes(app)
register_placement_routes(app)
register_ingest_routes(app)
register_admin_routes(app)
register_registry_routes(app)
register_search_routes(app)
if settings.suggestions_enabled:
    register_suggestions_routes(app)

# SEO document routes (/, /news/articles/:id, /section/:slug, robots, sitemaps).
# Registered last so nothing shadows the JSON API under /api/*; nginx decides
# which paths reach these vs. the static Flutter build.
register_seo_routes(app)


if __name__ == "__main__":
    app.start(host=settings.app_host, port=settings.app_port)
