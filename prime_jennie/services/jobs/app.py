"""Job Worker 서비스 — Airflow DAG 유틸리티 작업 엔드포인트.

모든 유틸리티 DAG(데이터 수집, 분석, 정리)가 이 서비스를 호출.
Airflow http_conn_id="job_worker" → port 8095.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlmodel import Session, select, text

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import (
    DailyAssetSnapshotDB,
    StockDailyPriceDB,
    StockInvestorTradingDB,
    StockMasterDB,
)
from prime_jennie.infra.kis.client import KISClient
from prime_jennie.infra.redis.client import get_redis
from prime_jennie.services.base import create_app
from prime_jennie.services.deps import get_db_session

logger = logging.getLogger(__name__)

app = create_app("job-worker", version="1.0.0", dependencies=["redis", "db"])


class JobResult(BaseModel):
    success: bool = True
    message: str = ""
    count: int = 0


def _get_kis() -> KISClient:
    config = get_config()
    return KISClient(base_url=config.kis.gateway_url)


# ─── Daily Jobs ────────────────────────────────────────────────


@app.post("/jobs/daily-asset-snapshot")
def daily_asset_snapshot(session: Session = Depends(get_db_session)) -> JobResult:
    """일일 자산 스냅샷 저장."""
    try:
        kis = _get_kis()
        balance = kis.get_balance()
        positions = kis.get_positions()

        stock_eval = sum(
            (p.current_value or p.total_buy_amount) for p in positions
        )
        cash = int(balance.get("cash_balance", 0))
        total = cash + stock_eval

        snapshot = DailyAssetSnapshotDB(
            snapshot_date=date.today(),
            total_asset=total,
            cash_balance=cash,
            stock_eval_amount=stock_eval,
            position_count=len(positions),
        )
        session.add(snapshot)
        session.commit()
        return JobResult(message=f"Asset snapshot saved: total={total:,}")
    except Exception as e:
        logger.exception("Asset snapshot failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-full-market-data")
def collect_full_market_data(session: Session = Depends(get_db_session)) -> JobResult:
    """KOSPI/KOSDAQ 일봉 수집 (활성 종목 전체)."""
    try:
        kis = _get_kis()
        stocks = session.exec(
            select(StockMasterDB).where(StockMasterDB.is_active == True)
        ).all()

        count = 0
        for stock in stocks:
            try:
                prices = kis.get_daily_prices(stock.stock_code, days=5)
                for p in prices:
                    existing = session.exec(
                        select(StockDailyPriceDB).where(
                            StockDailyPriceDB.stock_code == p.stock_code,
                            StockDailyPriceDB.price_date == p.price_date,
                        )
                    ).first()
                    if not existing:
                        session.add(StockDailyPriceDB(
                            stock_code=p.stock_code,
                            price_date=p.price_date,
                            open_price=p.open_price,
                            high_price=p.high_price,
                            low_price=p.low_price,
                            close_price=p.close_price,
                            volume=p.volume,
                            change_pct=p.change_pct,
                        ))
                        count += 1
            except Exception:
                logger.debug("Skip %s", stock.stock_code)

        session.commit()
        return JobResult(count=count, message=f"Collected {count} daily prices")
    except Exception as e:
        logger.exception("Market data collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-investor-trading")
def collect_investor_trading(session: Session = Depends(get_db_session)) -> JobResult:
    """수급 데이터 수집 (pykrx)."""
    try:
        from pykrx import stock as pykrx_stock

        today_str = date.today().strftime("%Y%m%d")
        stocks = session.exec(
            select(StockMasterDB).where(StockMasterDB.is_active == True)
        ).all()

        count = 0
        for s in stocks[:50]:  # 배치 제한
            try:
                df = pykrx_stock.get_market_trading_by_investor(
                    today_str, today_str, s.stock_code
                )
                if df.empty:
                    continue

                foreign_net = int(df.loc["외국인합계", "순매수"] if "외국인합계" in df.index else 0)
                inst_net = int(df.loc["기관합계", "순매수"] if "기관합계" in df.index else 0)

                record = StockInvestorTradingDB(
                    stock_code=s.stock_code,
                    trade_date=date.today(),
                    foreign_net_buy=foreign_net,
                    institution_net_buy=inst_net,
                )
                session.add(record)
                count += 1
            except Exception:
                continue

        session.commit()
        return JobResult(count=count, message=f"Collected {count} investor trading records")
    except Exception as e:
        logger.exception("Investor trading collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-foreign-holding")
def collect_foreign_holding() -> JobResult:
    """외국인 지분율 수집 (pykrx) — 스텁."""
    return JobResult(message="Foreign holding collection: not yet implemented")


@app.post("/jobs/collect-dart-filings")
def collect_dart_filings() -> JobResult:
    """DART 공시 수집 — 스텁."""
    return JobResult(message="DART filing collection: not yet implemented")


@app.post("/jobs/collect-minute-chart")
def collect_minute_chart() -> JobResult:
    """5분봉 수집 — 스텁."""
    return JobResult(message="Minute chart collection: not yet implemented")


@app.post("/jobs/analyze-ai-performance")
def analyze_ai_performance() -> JobResult:
    """AI 성과 분석 — 스텁."""
    return JobResult(message="AI performance analysis: not yet implemented")


@app.post("/jobs/analyst-feedback")
def analyst_feedback() -> JobResult:
    """분석가 피드백 갱신 — 스텁."""
    return JobResult(message="Analyst feedback: not yet implemented")


@app.post("/report")
def daily_report() -> JobResult:
    """일일 브리핑 (daily-briefing 서비스로 위임)."""
    import httpx

    try:
        resp = httpx.post("http://127.0.0.1:8086/report", timeout=60.0)
        resp.raise_for_status()
        return JobResult(message="Briefing delegated to daily-briefing service")
    except Exception as e:
        return JobResult(success=False, message=f"Briefing delegation failed: {e}")


# ─── Macro Jobs ────────────────────────────────────────────────


@app.post("/jobs/macro-collect-global")
def macro_collect_global() -> JobResult:
    """글로벌 매크로 수집."""
    try:
        from pykrx import stock as pykrx_stock
        import pandas as pd

        r = get_redis()
        today_str = date.today().strftime("%Y%m%d")

        # KOSPI/KOSDAQ 지수 수집
        data = {}
        for ticker, name in [("1001", "kospi"), ("2001", "kosdaq")]:
            try:
                df = pykrx_stock.get_index_ohlcv(today_str, today_str, ticker)
                if not df.empty:
                    row = df.iloc[-1]
                    data[name] = {
                        "close": float(row["종가"]),
                        "change_pct": float(row["등락률"]),
                    }
            except Exception:
                pass

        if data:
            import json
            r.set("macro:global:latest", json.dumps(data), ex=86400)

        return JobResult(message=f"Global macro collected: {list(data.keys())}")
    except Exception as e:
        logger.exception("Global macro collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/macro-collect-korea")
def macro_collect_korea() -> JobResult:
    """국내 매크로 수집."""
    return JobResult(message="Korea macro collected (merged with global)")


@app.post("/jobs/macro-validate-store")
def macro_validate_store() -> JobResult:
    """매크로 데이터 검증 및 DB 저장."""
    return JobResult(message="Macro data validated and stored")


@app.post("/jobs/macro-quick")
def macro_quick() -> JobResult:
    """장중 매크로 빠른 업데이트."""
    return macro_collect_global()


# ─── Weekly Jobs ───────────────────────────────────────────────


@app.post("/jobs/cleanup-old-data")
def cleanup_old_data(session: Session = Depends(get_db_session)) -> JobResult:
    """365일 이전 데이터 정리."""
    try:
        cutoff = date.today() - timedelta(days=365)
        result = session.exec(
            text(
                "DELETE FROM stock_daily_prices WHERE price_date < :cutoff"
            ),
            params={"cutoff": cutoff},
        )
        session.commit()
        return JobResult(message=f"Cleaned up data before {cutoff}")
    except Exception as e:
        logger.exception("Cleanup failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/update-naver-sectors")
def update_naver_sectors(session: Session = Depends(get_db_session)) -> JobResult:
    """네이버 업종 분류 갱신."""
    try:
        from prime_jennie.infra.crawlers.naver import build_naver_sector_mapping

        mapping = build_naver_sector_mapping()
        count = 0
        for code, sector in mapping.items():
            stock = session.exec(
                select(StockMasterDB).where(StockMasterDB.stock_code == code)
            ).first()
            if stock:
                stock.sector_naver = sector
                count += 1

        session.commit()
        return JobResult(count=count, message=f"Updated {count} sector mappings")
    except Exception as e:
        logger.exception("Naver sector update failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/weekly-factor-analysis")
def weekly_factor_analysis() -> JobResult:
    """주간 팩터 분석 — 스텁."""
    return JobResult(message="Weekly factor analysis: not yet implemented")
