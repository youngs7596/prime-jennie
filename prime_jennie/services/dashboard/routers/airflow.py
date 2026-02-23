"""Airflow API — DAG 목록 조회 및 수동 트리거 (Airflow 3 / REST v2)."""

import logging
import os
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/airflow", tags=["airflow"])

logger = logging.getLogger(__name__)

AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://localhost:8085")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "admin")
AIRFLOW_PASS = os.getenv("AIRFLOW_PASS", "admin")

# JWT 토큰 캐시 (프로세스 수명 동안 재사용, 만료 시 재발급)
_cached_token: str | None = None


async def _get_token(client: httpx.AsyncClient) -> str:
    """Airflow 3 JWT 토큰 발급."""
    global _cached_token  # noqa: PLW0603
    if _cached_token:
        return _cached_token
    resp = await client.post(
        f"{AIRFLOW_URL}/auth/token",
        json={"username": AIRFLOW_USER, "password": AIRFLOW_PASS},
    )
    resp.raise_for_status()
    _cached_token = resp.json()["access_token"]
    return _cached_token


async def _airflow_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    **kwargs: object,
) -> httpx.Response:
    """Bearer 인증 포함 Airflow API 요청. 401 시 토큰 재발급 1회 재시도."""
    global _cached_token  # noqa: PLW0603
    token = await _get_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.request(method, f"{AIRFLOW_URL}{path}", headers=headers, **kwargs)

    if resp.status_code == 401:
        _cached_token = None
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.request(method, f"{AIRFLOW_URL}{path}", headers=headers, **kwargs)

    return resp


async def _get_dags() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await _airflow_request(client, "GET", "/api/v2/dags?limit=100")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Airflow dags fetch failed: %s", e)
            return {"dags": []}


async def _get_dag_runs(dag_id: str, limit: int = 1) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await _airflow_request(
                client,
                "GET",
                f"/api/v2/dags/{dag_id}/dagRuns",
                params={"limit": limit, "order_by": "-logical_date"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Airflow dag runs fetch failed for %s: %s", dag_id, e)
            return {"dag_runs": []}


@router.get("/dags")
async def list_dags():
    """활성 DAG 목록 + 최근 실행 상태."""
    dags_data = await _get_dags()
    results = []

    for dag in dags_data.get("dags", []):
        if dag.get("is_paused"):
            continue

        dag_id = dag["dag_id"]
        runs = await _get_dag_runs(dag_id, limit=1)
        last_run = (runs.get("dag_runs") or [{}])[0]

        results.append(
            {
                "dag_id": dag_id,
                "description": dag.get("description"),
                "schedule_interval": dag.get("timetable_summary"),
                "next_dagrun": dag.get("next_dagrun_run_after"),
                "last_run_state": last_run.get("state", "unknown"),
                "last_run_date": last_run.get("logical_date"),
            }
        )

    return results


@router.post("/dags/{dag_id}/trigger")
async def trigger_dag(dag_id: str):
    """DAG 수동 트리거."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = await _airflow_request(
                client,
                "POST",
                f"/api/v2/dags/{dag_id}/dagRuns",
                json={"logical_date": now, "conf": {}},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.text,
            ) from exc
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
