"""Price Monitor DAGs — 시작/중지 라이프사이클.

price_monitor_ops: 09:00 KST 시작
price_monitor_stop_ops: 15:30 KST 중지
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.http.operators.http import SimpleHttpOperator

from airflow_utils import get_default_args

local_tz = pendulum.timezone("Asia/Seoul")

with DAG(
    dag_id="price_monitor_ops",
    default_args=get_default_args(retries=3, retry_delay=timedelta(minutes=1)),
    description="가격 모니터 시작",
    schedule="0 9 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["trading", "monitor"],
) as dag_start:
    start_monitor = SimpleHttpOperator(
        task_id="start_monitor",
        http_conn_id="price_monitor",
        endpoint="/start",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=1),
    )

with DAG(
    dag_id="price_monitor_stop_ops",
    default_args=get_default_args(retries=3, retry_delay=timedelta(minutes=1)),
    description="가격 모니터 중지",
    schedule="30 15 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
    catchup=False,
    tags=["trading", "monitor"],
) as dag_stop:
    stop_monitor = SimpleHttpOperator(
        task_id="stop_monitor",
        http_conn_id="price_monitor",
        endpoint="/stop",
        method="POST",
        headers={"Content-Type": "application/json"},
        response_check=lambda resp: resp.status_code == 200,
        execution_timeout=timedelta(minutes=1),
    )
