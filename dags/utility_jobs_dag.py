"""Utility Jobs DAGs — 데이터 수집, 정리, 분석.

모든 유틸리티 작업은 job-worker:8095 를 통해 실행.
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.http.operators.http import HttpOperator
from airflow_utils import get_default_args

local_tz = pendulum.timezone("Asia/Seoul")


def _utility_dag(
    dag_id: str,
    schedule: str,
    description: str,
    endpoint: str,
    timeout_min: int = 5,
    retries: int = 1,
    tags: list | None = None,
    data: str | None = None,
) -> DAG:
    """유틸리티 DAG 팩토리."""
    with DAG(
        dag_id=dag_id,
        default_args=get_default_args(retries=retries, retry_delay=timedelta(minutes=5)),
        description=description,
        schedule=schedule,
        start_date=pendulum.datetime(2026, 1, 1, tz=local_tz),
        catchup=False,
        tags=tags or ["utility"],
    ) as dag:
        HttpOperator(
            task_id=dag_id.replace("-", "_"),
            http_conn_id="job_worker",
            endpoint=endpoint,
            method="POST",
            data=data or "{}",
            headers={"Content-Type": "application/json"},
            response_check=lambda resp: resp.status_code == 200,
            execution_timeout=timedelta(minutes=timeout_min),
        )
    return dag


# ─── Daily Jobs (Mon-Fri) ────────────────────────────────────

daily_asset_snapshot = _utility_dag(
    "daily_asset_snapshot",
    "45 15 * * 1-5",
    "일일 자산 스냅샷 (총자산, 현금, 주식평가)",
    "/jobs/daily-asset-snapshot",
    timeout_min=5,
    tags=["portfolio", "daily"],
)

refresh_market_caps = _utility_dag(
    "refresh_market_caps",
    "50 15 * * 1-5",
    "시가총액 갱신 (KIS snapshot → stock_masters)",
    "/jobs/refresh-market-caps",
    timeout_min=10,
    retries=2,
    tags=["data", "daily"],
)

daily_market_data_collector = _utility_dag(
    "daily_market_data_collector",
    "0 16 * * 1-5",
    "KOSPI/KOSDAQ 일봉 수집",
    "/jobs/collect-full-market-data",
    timeout_min=10,
    retries=2,
    tags=["data", "daily"],
)

daily_briefing_report = _utility_dag(
    "daily_briefing_report",
    "0 17 * * 1-5",
    "일일 브리핑 발송",
    "/report",
    timeout_min=5,
    retries=2,
    tags=["briefing", "daily"],
)

daily_ai_performance = _utility_dag(
    "daily_ai_performance_analysis",
    "0 7 * * 1-5",
    "AI 의사결정 성과 분석",
    "/jobs/analyze-ai-performance",
    timeout_min=5,
    retries=2,
    tags=["analytics", "daily"],
)

collect_investor_trading = _utility_dag(
    "collect_investor_trading",
    "30 18 * * 1-5",
    "수급 데이터 수집 (300종목)",
    "/jobs/collect-investor-trading",
    timeout_min=15,
    tags=["data", "daily"],
)

collect_foreign_holding = _utility_dag(
    "collect_foreign_holding_ratio",
    "0 19 * * 1-5",
    "외국인 지분율 수집 (300종목)",
    "/jobs/collect-foreign-holding",
    timeout_min=15,
    tags=["data", "daily"],
)

collect_dart = _utility_dag(
    "collect_dart_filings",
    "45 18 * * 1-5",
    "DART 공시 수집",
    "/jobs/collect-dart-filings",
    timeout_min=5,
    tags=["data", "daily"],
)

analyst_feedback = _utility_dag(
    "analyst_feedback_update",
    "0 18 * * 1-5",
    "분석가 피드백 갱신",
    "/jobs/analyst-feedback",
    timeout_min=2,
    tags=["analytics", "daily"],
)

# ─── Intraday Jobs ──────────────────────────────────────────

collect_minute_chart = _utility_dag(
    "collect_minute_chart",
    "*/5 9-15 * * 1-5",
    "5분봉 수집 (백테스트용, 상위 30종목)",
    "/jobs/collect-minute-chart",
    timeout_min=3,
    tags=["data", "intraday"],
)

# ─── Weekly Jobs ────────────────────────────────────────────

data_cleanup_weekly = _utility_dag(
    "data_cleanup_weekly",
    "0 3 * * 0",
    "오래된 데이터 정리 (365일+)",
    "/jobs/cleanup-old-data",
    timeout_min=10,
    tags=["maintenance", "weekly"],
)

update_naver_sectors = _utility_dag(
    "update_naver_sectors_weekly",
    "0 20 * * 0",
    "네이버 업종 분류 갱신 (79개 세분류)",
    "/jobs/update-naver-sectors",
    timeout_min=15,
    retries=2,
    tags=["data", "weekly"],
)

weekly_factor_analysis = _utility_dag(
    "weekly_factor_analysis",
    "0 22 * * 5",
    "주간 팩터 분석",
    "/jobs/weekly-factor-analysis",
    timeout_min=30,
    retries=2,
    tags=["analytics", "weekly"],
)

collect_consensus = _utility_dag(
    "collect_consensus",
    "0 6 * * 1,4",
    "컨센서스 수집 (Forward PER/EPS/ROE) — 월/목 06:00",
    "/jobs/collect-consensus",
    timeout_min=30,
    retries=2,
    tags=["data", "weekly"],
)

# ─── Monthly Jobs ──────────────────────────────────────────

collect_naver_roe = _utility_dag(
    "collect_naver_roe_monthly",
    "0 3 1 * *",
    "월간 ROE 수집 (네이버 금융 크롤링)",
    "/jobs/collect-naver-roe",
    timeout_min=30,
    retries=2,
    tags=["data", "monthly"],
)
