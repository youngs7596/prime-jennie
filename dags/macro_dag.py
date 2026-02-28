"""Macro Collection + Council DAG.

enhanced_macro_collection: 07:40, 11:40 KST 월-금
macro_council: 07:50, 11:50 KST 월-금 (수집 10분 후)
enhanced_macro_quick: 09:30-14:30 KST 매시 (장중 빠른 업데이트)
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.http.operators.http import HttpOperator
from airflow_utils import get_default_args

local_tz = pendulum.timezone("Asia/Seoul")

# ─── Enhanced Macro Collection ────────────────────────────────

with DAG(
    dag_id="enhanced_macro_collection",
    default_args=get_default_args(retries=2, retry_delay=timedelta(minutes=5)),
    description="글로벌+국내 매크로 데이터 수집 및 검증",
    schedule="40 7,11 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["macro", "data"],
) as dag_collect:
    collect_global = HttpOperator(
        task_id="collect_global",
        http_conn_id="job_worker",
        endpoint="/jobs/macro-collect-global",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=5),
    )

    collect_korea = HttpOperator(
        task_id="collect_korea",
        http_conn_id="job_worker",
        endpoint="/jobs/macro-collect-korea",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=5),
    )

    validate_and_store = HttpOperator(
        task_id="validate_and_store",
        http_conn_id="job_worker",
        endpoint="/jobs/macro-validate-store",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=3),
    )

    [collect_global, collect_korea] >> validate_and_store


# ─── Macro Council ────────────────────────────────────────────

with DAG(
    dag_id="macro_council",
    default_args=get_default_args(retries=1, retry_delay=timedelta(minutes=5)),
    description="3현자 매크로 분석 (Council Pipeline)",
    schedule="50 7,11 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["macro", "council"],
) as dag_council:
    run_council = HttpOperator(
        task_id="run_council",
        http_conn_id="job_worker",
        endpoint="/jobs/council-trigger",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=10),
    )


# ─── Enhanced Macro Quick ────────────────────────────────────

with DAG(
    dag_id="enhanced_macro_quick",
    default_args=get_default_args(retries=2, retry_delay=timedelta(minutes=2)),
    description="장중 매크로 빠른 업데이트 (naver)",
    schedule="30 9-14 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["macro", "intraday"],
) as dag_quick:
    macro_quick = HttpOperator(
        task_id="macro_quick",
        http_conn_id="job_worker",
        endpoint="/jobs/macro-quick",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=2),
    )
