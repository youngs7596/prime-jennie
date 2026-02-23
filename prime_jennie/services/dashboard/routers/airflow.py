"""Airflow API — DAG 목록 조회 및 수동 트리거."""

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/airflow", tags=["airflow"])

logger = logging.getLogger(__name__)

AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://localhost:8085")
AIRFLOW_AUTH = ("admin", "admin")


async def _get_dags() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AIRFLOW_URL}/api/v1/dags?limit=100",
                auth=AIRFLOW_AUTH,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Airflow dags fetch failed: %s", e)
            return {"dags": []}


async def _get_dag_runs(dag_id: str, limit: int = 1) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns",
                params={"limit": limit, "order_by": "-execution_date"},
                auth=AIRFLOW_AUTH,
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
                "schedule_interval": dag.get("schedule_interval"),
                "next_dagrun": dag.get("next_dagrun"),
                "last_run_state": last_run.get("state", "unknown"),
                "last_run_date": last_run.get("execution_date"),
            }
        )

    return results


@router.post("/dags/{dag_id}/trigger")
async def trigger_dag(dag_id: str):
    """DAG 수동 트리거."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{AIRFLOW_URL}/api/v1/dags/{dag_id}/dagRuns",
                auth=AIRFLOW_AUTH,
                json={"conf": {}},
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
