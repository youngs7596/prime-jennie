"""Daily Briefing — 일일 트레이딩 리포트 생성 및 발송.

데이터 수집 → LLM 요약 → 텔레그램 전송.
"""

import logging
from datetime import date

from fastapi import Depends
from sqlmodel import Session

from prime_jennie.services.base import create_app
from prime_jennie.services.deps import get_db_session

from .reporter import DailyReporter

logger = logging.getLogger(__name__)

app = create_app("daily-briefing", version="1.0.0", dependencies=["db"])

_reporter: DailyReporter | None = None


def _get_reporter() -> DailyReporter:
    global _reporter
    if _reporter is None:
        _reporter = DailyReporter()
    return _reporter


@app.post("/report")
async def trigger_report(session: Session = Depends(get_db_session)):
    """일일 리포트 생성 및 발송 (Airflow 트리거)."""
    reporter = _get_reporter()
    try:
        result = await reporter.create_and_send_report(session)
        return {"status": "success", "report_date": str(date.today()), **result}
    except Exception as e:
        logger.exception("Report generation failed")
        return {"status": "error", "message": str(e)}


@app.get("/report/preview")
async def preview_report(session: Session = Depends(get_db_session)):
    """리포트 미리보기 (발송하지 않음)."""
    reporter = _get_reporter()
    try:
        data = reporter.collect_report_data(session)
        context = reporter._build_data_context(data)
        fallback = reporter._format_fallback_html(data)
        return {"status": "ok", "data": data, "context": context, "preview": fallback}
    except Exception as e:
        return {"status": "error", "message": str(e)}
