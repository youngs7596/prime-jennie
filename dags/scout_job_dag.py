"""Scout Job DAG — AI 종목 발굴 (장중 1시간 간격).

Schedule: 08:30-14:30 KST, 월-금
Target: scout-job:8087 /trigger
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.http.operators.http import SimpleHttpOperator

from airflow_utils import get_default_args

local_tz = pendulum.timezone("Asia/Seoul")

with DAG(
    dag_id="scout_job_v1",
    default_args=get_default_args(retries=1, retry_delay=timedelta(minutes=5)),
    description="AI 종목 스캔 (KOSPI+KOSDAQ, Unified Analyst)",
    schedule="30 8-14 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["trading", "scout"],
) as dag:
    trigger_scout = SimpleHttpOperator(
        task_id="trigger_scout",
        http_conn_id="scout_job",
        endpoint="/trigger",
        method="POST",
        data='{"source": "airflow"}',
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=15),
    )
