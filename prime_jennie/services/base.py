"""FastAPI 앱 팩토리 — 모든 서비스의 공통 패턴.

Usage:
    from prime_jennie.services.base import create_app

    app = create_app("scout-job", version="1.0.0", dependencies=["redis", "db"])
"""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from prime_jennie.domain.health import DependencyHealth, HealthStatus

logger = logging.getLogger(__name__)

# 서비스 시작 시각 (uptime 계산용)
_start_time: float = 0.0


def create_app(
    service_name: str,
    *,
    version: str = "1.0.0",
    lifespan: Callable | None = None,
    dependencies: list[str] | None = None,
) -> FastAPI:
    """FastAPI 앱 팩토리 — 공통 헬스체크 + 에러 핸들러.

    Args:
        service_name: 서비스 식별자 (예: "kis-gateway", "scout-job")
        version: 서비스 버전
        lifespan: 커스텀 lifespan context manager (startup/shutdown)
        dependencies: 헬스체크에 포함할 의존성 목록 ("redis", "db", "kis")
    """
    deps = dependencies or []

    @asynccontextmanager
    async def default_lifespan(app: FastAPI) -> AsyncIterator[None]:
        global _start_time
        _start_time = time.monotonic()
        logger.info("[%s] Starting v%s", service_name, version)
        yield
        logger.info("[%s] Shutting down", service_name)

    @asynccontextmanager
    async def wrapped_lifespan(app: FastAPI) -> AsyncIterator[None]:
        global _start_time
        _start_time = time.monotonic()
        logger.info("[%s] Starting v%s", service_name, version)
        if lifespan:
            async with lifespan(app):
                yield
        else:
            yield
        logger.info("[%s] Shutting down", service_name)

    chosen_lifespan = wrapped_lifespan if lifespan else default_lifespan

    app = FastAPI(
        title=f"prime-jennie {service_name}",
        version=version,
        lifespan=chosen_lifespan,
    )

    # --- Error Handlers ---

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": exc.errors(), "message": "Validation error"},
        )

    @app.exception_handler(httpx.HTTPStatusError)
    async def http_status_error_handler(request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "detail": str(exc),
                "message": f"Upstream error: {exc.response.status_code}",
            },
        )

    # --- Health Check ---

    @app.get("/health")
    async def health() -> HealthStatus:
        dep_health: dict[str, DependencyHealth] = {}
        overall = "healthy"

        for dep in deps:
            dep_health[dep] = _check_dependency(dep)
            if dep_health[dep].status == "down":
                overall = "unhealthy"
            elif dep_health[dep].status == "degraded" and overall == "healthy":
                overall = "degraded"

        return HealthStatus(
            service=service_name,
            status=overall,
            uptime_seconds=time.monotonic() - _start_time,
            version=version,
            dependencies=dep_health,
            timestamp=datetime.now(UTC),
        )

    return app


def _check_dependency(name: str) -> DependencyHealth:
    """의존성 상태 체크."""
    start = time.monotonic()
    try:
        if name == "redis":
            from prime_jennie.infra.redis.client import get_redis

            r = get_redis()
            r.ping()
        elif name == "db":
            from sqlalchemy import text

            from prime_jennie.infra.database.engine import get_engine

            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        elif name == "kis":
            from prime_jennie.infra.kis.client import KISClient

            client = KISClient()
            if not client.health():
                return DependencyHealth(
                    status="down",
                    latency_ms=(time.monotonic() - start) * 1000,
                    message="KIS Gateway unreachable",
                )
        else:
            return DependencyHealth(status="healthy", message=f"Unknown dep: {name}")

        latency = (time.monotonic() - start) * 1000
        status = "healthy" if latency < 1000 else "degraded"
        return DependencyHealth(status=status, latency_ms=round(latency, 1))

    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return DependencyHealth(
            status="down",
            latency_ms=round(latency, 1),
            message=str(e)[:200],
        )
