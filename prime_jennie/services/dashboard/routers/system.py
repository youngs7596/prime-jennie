"""System API — 서비스 헬스 체크, 시스템 상태."""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/system", tags=["system"])

logger = logging.getLogger(__name__)

# 모니터링 대상 서비스 목록 (이름, 포트)
_SERVICES = [
    ("kis-gateway", 8080),
    ("buy-scanner", 8081),
    ("buy-executor", 8082),
    ("sell-executor", 8083),
    ("daily-briefing", 8086),
    ("scout-job", 8087),
    ("price-monitor", 8088),
    ("macro-council", 8089),
    ("dashboard", 8090),
    ("telegram", 8091),
    ("news-pipeline", 8092),
    ("job-worker", 8095),
]


class ServiceStatus(BaseModel):
    """개별 서비스 상태."""

    name: str
    port: int
    status: str  # "healthy" | "unhealthy" | "unreachable"
    version: Optional[str] = None
    uptime_seconds: Optional[float] = None
    message: Optional[str] = None


@router.get("/health")
async def get_all_health() -> list[ServiceStatus]:
    """모든 서비스의 헬스 상태 조회."""
    results = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, port in _SERVICES:
            results.append(await _check_service(client, name, port))
    return results


async def _check_service(
    client: httpx.AsyncClient, name: str, port: int
) -> ServiceStatus:
    """개별 서비스 헬스 체크."""
    try:
        resp = await client.get(f"http://localhost:{port}/health")
        if resp.status_code == 200:
            data = resp.json()
            return ServiceStatus(
                name=name,
                port=port,
                status=data.get("status", "healthy"),
                version=data.get("version"),
                uptime_seconds=data.get("uptime_seconds"),
            )
        return ServiceStatus(
            name=name,
            port=port,
            status="unhealthy",
            message=f"HTTP {resp.status_code}",
        )
    except httpx.ConnectError:
        return ServiceStatus(
            name=name, port=port, status="unreachable", message="Connection refused"
        )
    except Exception as e:
        return ServiceStatus(
            name=name, port=port, status="unreachable", message=str(e)[:100]
        )
