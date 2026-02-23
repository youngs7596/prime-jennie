"""Logs API — Loki 로그 조회 프록시."""

import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/logs", tags=["logs"])

logger = logging.getLogger(__name__)

LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")

# promtail 설정의 app 라벨 기준 서비스 목록
_SERVICES = [
    "kis-gateway",
    "scout-job",
    "buy-scanner",
    "buy-executor",
    "sell-executor",
    "price-monitor",
    "macro-council",
    "news-pipeline",
    "dashboard",
    "job-worker",
    "airflow-webserver",
    "airflow-scheduler",
    "daily-briefing",
    "telegram",
]


@router.get("/stream")
async def get_logs(
    service: str = Query(..., description="Loki app 라벨 (서비스명)"),
    limit: int = Query(100, description="반환할 로그 라인 수"),
    start: int | None = Query(None, description="시작 타임스탬프 (ns)"),
    end: int | None = Query(None, description="끝 타임스탬프 (ns)"),
):
    """Loki에서 특정 서비스의 로그를 조회."""
    if not start:
        start = int((time.time() - 3600) * 1e9)
    if not end:
        end = int(time.time() * 1e9)

    query = f'{{app="{service}"}}'

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "limit": limit,
                    "start": start,
                    "end": end,
                    "direction": "BACKWARD",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            logs = []
            if "data" in data and "result" in data["data"]:
                for result in data["data"]["result"]:
                    for val in result["values"]:
                        logs.append({"timestamp": val[0], "message": val[1]})

            return {"logs": logs}

        except httpx.RequestError as exc:
            logger.warning("Loki connection error: %s", exc)
            raise HTTPException(status_code=502, detail="Could not connect to logging service") from exc
        except Exception as e:
            logger.warning("Error fetching logs: %s", e)
            raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/services")
async def list_services():
    """사용 가능한 서비스 목록 반환."""
    return {"services": _SERVICES}
