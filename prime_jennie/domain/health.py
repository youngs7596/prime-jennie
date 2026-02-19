"""헬스 체크 모델."""

from datetime import datetime

from pydantic import BaseModel


class DependencyHealth(BaseModel):
    """의존 서비스 상태."""

    status: str  # "healthy" | "degraded" | "down"
    latency_ms: float | None = None
    message: str | None = None


class HealthStatus(BaseModel):
    """서비스 헬스 상태."""

    service: str
    status: str  # "healthy" | "degraded" | "unhealthy"
    uptime_seconds: float
    version: str = "1.0.0"
    dependencies: dict[str, DependencyHealth] = {}
    timestamp: datetime
