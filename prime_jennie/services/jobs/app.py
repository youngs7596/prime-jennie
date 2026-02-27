"""Job Worker 서비스 — Airflow DAG 유틸리티 작업 엔드포인트.

모든 유틸리티 DAG(데이터 수집, 분석, 정리)가 이 서비스를 호출.
Airflow http_conn_id="job_worker" → port 8095.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from fastapi import Depends
from pydantic import BaseModel
from sqlmodel import Session, col, select, text

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.models import (
    DailyAssetSnapshotDB,
    DailyQuantScoreDB,
    PositionDB,
    StockConsensusDB,
    StockDailyPriceDB,
    StockDisclosureDB,
    StockFundamentalDB,
    StockInvestorTradingDB,
    StockMasterDB,
    StockMinutePriceDB,
    TradeLogDB,
    WatchlistHistoryDB,
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
    """일일 자산 스냅샷 저장.

    KIS API의 tot_evlu_amt(총 평가금액)을 직접 사용.
    cash + stock_eval 수동 합산은 예수금 이중 계산 문제가 있어 사용하지 않음.
    손익 계산: 미실현(포지션) + 실현(당일 매도 profit_amount 합산).
    """
    try:
        kis = _get_kis()
        balance = kis.get_balance()
        positions = balance.get("positions", [])

        total = int(balance.get("total_asset", 0))
        cash = int(balance.get("cash_balance", 0))
        stock_eval = int(balance.get("stock_eval_amount", 0))

        # total_asset이 0이면 fallback (API 응답 이상)
        if total <= 0:
            total = cash + stock_eval

        # 미실현 손익: 보유 포지션의 (현재가 - 매입가) 합산
        unrealized_pnl = 0
        for p in positions:
            current_val = int(p.get("current_value", 0))
            buy_amt = int(p.get("total_buy_amount", 0))
            unrealized_pnl += current_val - buy_amt

        # 실현 손익: 당일 매도 거래의 profit_amount 합산
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        realized_pnl_row = session.exec(
            select(text("COALESCE(SUM(profit_amount), 0)"))
            .select_from(TradeLogDB)
            .where(TradeLogDB.trade_type == "SELL")
            .where(col(TradeLogDB.trade_timestamp).between(today_start, today_end))
        ).one()
        realized_pnl = int(realized_pnl_row)

        total_pnl = unrealized_pnl + realized_pnl

        # UPSERT: 같은 날 재실행 시 UPDATE
        existing = session.get(DailyAssetSnapshotDB, today)
        if existing:
            existing.total_asset = total
            existing.cash_balance = cash
            existing.stock_eval_amount = stock_eval
            existing.position_count = len(positions)
            existing.total_profit_loss = total_pnl
            existing.realized_profit_loss = realized_pnl
        else:
            session.add(
                DailyAssetSnapshotDB(
                    snapshot_date=today,
                    total_asset=total,
                    cash_balance=cash,
                    stock_eval_amount=stock_eval,
                    position_count=len(positions),
                    total_profit_loss=total_pnl,
                    realized_profit_loss=realized_pnl,
                )
            )
        session.commit()

        logger.info(
            "Asset snapshot: total=%s, unrealized=%s, realized=%s",
            f"{total:,}",
            f"{unrealized_pnl:,}",
            f"{realized_pnl:,}",
        )
        return JobResult(
            message=f"Asset snapshot saved: total={total:,}, P&L={total_pnl:,} (realized={realized_pnl:,})"
        )
    except Exception as e:
        logger.exception("Asset snapshot failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-full-market-data")
def collect_full_market_data(session: Session = Depends(get_db_session)) -> JobResult:
    """KOSPI/KOSDAQ 일봉 수집 (활성 종목 전체).

    Rate limit: Gateway 19/sec 제한 → 18/sec pacing.
    """
    try:
        kis = _get_kis()
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()
        logger.info("Collecting daily prices for top %d stocks by market cap", len(stocks))

        count = 0
        failed = 0
        min_interval = 1.0 / 18  # 18 req/sec (gateway 19/sec 미만)
        last_request = 0.0
        batch_size = 100  # 100건마다 중간 커밋

        for i, stock in enumerate(stocks):
            try:
                # Rate limiting
                now = time.monotonic()
                wait = last_request + min_interval - now
                if wait > 0:
                    time.sleep(wait)
                last_request = time.monotonic()

                prices = kis.get_daily_prices(stock.stock_code, days=30)
                for p in prices:
                    existing = session.exec(
                        select(StockDailyPriceDB).where(
                            StockDailyPriceDB.stock_code == p.stock_code,
                            StockDailyPriceDB.price_date == p.price_date,
                        )
                    ).first()
                    if not existing:
                        session.add(
                            StockDailyPriceDB(
                                stock_code=p.stock_code,
                                price_date=p.price_date,
                                open_price=p.open_price,
                                high_price=p.high_price,
                                low_price=p.low_price,
                                close_price=p.close_price,
                                volume=p.volume,
                                change_pct=p.change_pct,
                            )
                        )
                        count += 1
            except Exception as e:
                failed += 1
                logger.warning("Failed %s: %s", stock.stock_code, e)

            # 중간 커밋 + 진행 로그
            if (i + 1) % batch_size == 0:
                session.commit()
                logger.info(
                    "Progress: %d/%d stocks (collected=%d, failed=%d)",
                    i + 1,
                    len(stocks),
                    count,
                    failed,
                )

        session.commit()
        msg = f"Collected {count} daily prices from {len(stocks)} stocks (failed={failed})"
        logger.info(msg)
        return JobResult(count=count, message=msg)
    except Exception as e:
        logger.exception("Market data collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/refresh-market-caps")
def refresh_market_caps(session: Session = Depends(get_db_session)) -> JobResult:
    """시가총액 갱신 — KIS snapshot에서 market_cap 업데이트.

    시가총액 상위 300종목 대상. 18 req/sec rate limiting.
    """
    try:
        kis = _get_kis()
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()
        logger.info("Refreshing market caps for %d stocks", len(stocks))

        count = 0
        failed = 0
        min_interval = 1.0 / 18
        last_request = 0.0

        for i, stock in enumerate(stocks):
            try:
                now = time.monotonic()
                wait = last_request + min_interval - now
                if wait > 0:
                    time.sleep(wait)
                last_request = time.monotonic()

                snap = kis.get_price(stock.stock_code)
                if snap and snap.market_cap and snap.market_cap > 0:
                    stock.market_cap = snap.market_cap
                    stock.updated_at = datetime.utcnow()
                    count += 1
            except Exception as e:
                failed += 1
                logger.warning("Market cap failed %s: %s", stock.stock_code, e)

            if (i + 1) % 100 == 0:
                session.commit()
                logger.info("Market cap progress: %d/%d (updated=%d)", i + 1, len(stocks), count)

        session.commit()
        msg = f"Updated {count} market caps (failed={failed})"
        logger.info(msg)
        return JobResult(count=count, message=msg)
    except Exception as e:
        logger.exception("Market cap refresh failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-investor-trading")
def collect_investor_trading(session: Session = Depends(get_db_session)) -> JobResult:
    """수급 데이터 수집 (pykrx).

    최근 7일 직전 거래일 기준, 시가총액 상위 300종목.
    pykrx rate limit 방지: 종목당 0.5초 간격.
    """
    try:
        from pykrx import stock as pykrx_stock

        today_str = date.today().strftime("%Y%m%d")
        start_str = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()

        count = 0
        failed = 0
        for i, s in enumerate(stocks):
            try:
                if i > 0:
                    time.sleep(0.5)  # pykrx rate limit 방지
                df = pykrx_stock.get_market_trading_value_by_investor(start_str, today_str, s.stock_code)
                if df.empty:
                    continue

                foreign_net = int(df.loc["외국인", "순매수"] if "외국인" in df.index else 0)
                inst_net = int(df.loc["기관합계", "순매수"] if "기관합계" in df.index else 0)

                # 중복 방지: 직전 거래일 기준
                trade_date = date.today()
                existing = session.exec(
                    select(StockInvestorTradingDB).where(
                        StockInvestorTradingDB.stock_code == s.stock_code,
                        StockInvestorTradingDB.trade_date == trade_date,
                    )
                ).first()
                if existing:
                    existing.foreign_net_buy = foreign_net
                    existing.institution_net_buy = inst_net
                else:
                    session.add(
                        StockInvestorTradingDB(
                            stock_code=s.stock_code,
                            trade_date=trade_date,
                            foreign_net_buy=foreign_net,
                            institution_net_buy=inst_net,
                        )
                    )
                count += 1
            except Exception as e:
                failed += 1
                logger.warning("Investor trading failed %s: %s", s.stock_code, e)

            if (i + 1) % 100 == 0:
                session.commit()
                logger.info("Investor trading progress: %d/%d (collected=%d)", i + 1, len(stocks), count)

        session.commit()
        msg = f"Collected {count} investor trading records (failed={failed})"
        logger.info(msg)
        return JobResult(count=count, message=msg)
    except Exception as e:
        logger.exception("Investor trading collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-foreign-holding")
def collect_foreign_holding(session: Session = Depends(get_db_session)) -> JobResult:
    """외국인 지분율 수집 (pykrx). 시가총액 상위 300종목."""
    try:
        from pykrx import stock as pykrx_stock

        today_str = date.today().strftime("%Y%m%d")
        start_str = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()

        count = 0
        failed = 0
        for i, s in enumerate(stocks):
            try:
                if i > 0:
                    time.sleep(0.5)  # pykrx rate limit 방지
                df = pykrx_stock.get_exhaustion_rates_of_foreign_investment_by_date(start_str, today_str, s.stock_code)
                if df.empty:
                    continue

                # 최신 거래일 데이터
                last_row = df.iloc[-1]
                last_date = df.index[-1]
                trade_date = last_date.date() if hasattr(last_date, "date") else last_date
                ratio = float(last_row.get("지분율", 0))

                # 기존 investor_trading 레코드에 지분율 업데이트
                existing = session.exec(
                    select(StockInvestorTradingDB).where(
                        StockInvestorTradingDB.stock_code == s.stock_code,
                        StockInvestorTradingDB.trade_date == trade_date,
                    )
                ).first()

                if existing:
                    existing.foreign_holding_ratio = ratio
                else:
                    session.add(
                        StockInvestorTradingDB(
                            stock_code=s.stock_code,
                            trade_date=trade_date,
                            foreign_holding_ratio=ratio,
                        )
                    )
                count += 1
            except Exception as e:
                failed += 1
                logger.warning("Foreign holding failed %s: %s", s.stock_code, e)

            if (i + 1) % 100 == 0:
                session.commit()
                logger.info("Foreign holding progress: %d/%d (updated=%d)", i + 1, len(stocks), count)

        session.commit()
        msg = f"Updated {count} foreign holding ratios (failed={failed})"
        logger.info(msg)
        return JobResult(count=count, message=msg)
    except Exception as e:
        logger.exception("Foreign holding collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-dart-filings")
def collect_dart_filings(session: Session = Depends(get_db_session)) -> JobResult:
    """DART 공시 수집 (OpenDartReader)."""
    try:
        import OpenDartReader

        dart_api_key = os.getenv("DART_API_KEY", "")
        if not dart_api_key:
            return JobResult(success=False, message="DART_API_KEY not configured")

        dart = OpenDartReader(dart_api_key)
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        start_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")

        stocks = session.exec(select(StockMasterDB).where(StockMasterDB.is_active)).all()
        stock_codes = {s.stock_code for s in stocks}

        # 최근 7일 공시 목록 조회
        count = 0
        try:
            filings = dart.list(start=start_str, end=today_str, kind="A")
            if filings is None or (hasattr(filings, "empty") and filings.empty):
                return JobResult(message="No filings found")

            for _, row in filings.iterrows():
                corp_code = str(row.get("stock_code", "")).strip()
                if not corp_code or corp_code not in stock_codes:
                    continue

                receipt_no = str(row.get("rcept_no", ""))
                if not receipt_no:
                    continue

                # 중복 체크
                existing = session.exec(
                    select(StockDisclosureDB).where(StockDisclosureDB.receipt_no == receipt_no)
                ).first()
                if existing:
                    continue

                # 공시일 파싱
                date_str = str(row.get("rcept_dt", ""))
                try:
                    disc_date = datetime.strptime(date_str, "%Y%m%d").date()
                except ValueError:
                    disc_date = today

                session.add(
                    StockDisclosureDB(
                        stock_code=corp_code,
                        disclosure_date=disc_date,
                        title=str(row.get("report_nm", ""))[:500],
                        report_type=str(row.get("pblntf_ty", ""))[:50] or None,
                        receipt_no=receipt_no,
                        corp_name=str(row.get("corp_name", ""))[:100] or None,
                    )
                )
                count += 1

        except Exception as e:
            logger.warning("DART list query failed: %s", e)
            return JobResult(success=False, message=f"DART query failed: {e}")

        session.commit()
        return JobResult(count=count, message=f"Collected {count} DART filings")
    except Exception as e:
        logger.exception("DART filing collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-minute-chart")
def collect_minute_chart(session: Session = Depends(get_db_session)) -> JobResult:
    """5분봉 수집 — 시가총액 상위 30종목 + 워치리스트 종목 (백테스트용)."""
    try:
        kis = _get_kis()

        # 1) 시가총액 상위 30종목
        top30 = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(30)
        ).all()
        target_codes = {s.stock_code: s for s in top30}

        # 2) 최신 워치리스트 종목 추가
        latest_date_row = session.exec(
            select(WatchlistHistoryDB.snapshot_date)
            .order_by(WatchlistHistoryDB.snapshot_date.desc())  # type: ignore[union-attr]
            .limit(1)
        ).first()
        if latest_date_row:
            wl_codes = session.exec(
                select(WatchlistHistoryDB.stock_code).where(WatchlistHistoryDB.snapshot_date == latest_date_row)
            ).all()
            for code in wl_codes:
                if code not in target_codes:
                    master = session.get(StockMasterDB, code)
                    if master:
                        target_codes[code] = master

        stocks = list(target_codes.values())
        logger.info("Minute chart targets: %d (top30=%d + watchlist)", len(stocks), len(top30))

        count = 0
        failed = 0
        min_interval = 1.0 / 18
        last_request = 0.0
        for stock in stocks:
            try:
                now = time.monotonic()
                wait = last_request + min_interval - now
                if wait > 0:
                    time.sleep(wait)
                last_request = time.monotonic()

                prices = kis.get_minute_prices(stock.stock_code)
                for p in prices:
                    existing = session.exec(
                        select(StockMinutePriceDB).where(
                            StockMinutePriceDB.stock_code == p.stock_code,
                            StockMinutePriceDB.price_datetime == p.price_datetime,
                        )
                    ).first()
                    if not existing:
                        session.add(
                            StockMinutePriceDB(
                                stock_code=p.stock_code,
                                price_datetime=p.price_datetime,
                                open_price=p.open_price,
                                high_price=p.high_price,
                                low_price=p.low_price,
                                close_price=p.close_price,
                                volume=p.volume,
                            )
                        )
                        count += 1
            except Exception as e:
                failed += 1
                logger.warning("Minute chart failed %s: %s", stock.stock_code, e)

        session.commit()
        return JobResult(count=count, message=f"Collected {count} minute prices (failed={failed})")
    except Exception as e:
        logger.exception("Minute chart collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/analyze-ai-performance")
def analyze_ai_performance(session: Session = Depends(get_db_session)) -> JobResult:
    """AI 성과 분석 — 점수 구간별 승률, sell_reason별 성과, 국면별 수익률."""
    try:
        r = get_redis()

        # 최근 30일 매도 기록
        cutoff = date.today() - timedelta(days=30)
        sells = session.exec(
            select(TradeLogDB).where(
                TradeLogDB.trade_type == "SELL",
                TradeLogDB.trade_timestamp >= datetime.combine(cutoff, datetime.min.time()),
            )
        ).all()

        if not sells:
            return JobResult(message="No sell records in last 30 days")

        # 1. sell_reason별 성과
        reason_stats: dict = {}
        for t in sells:
            reason = t.reason or "UNKNOWN"
            if reason not in reason_stats:
                reason_stats[reason] = {"count": 0, "wins": 0, "total_pct": 0.0}
            reason_stats[reason]["count"] += 1
            pct = t.profit_pct or 0.0
            reason_stats[reason]["total_pct"] += pct
            if pct > 0:
                reason_stats[reason]["wins"] += 1

        for v in reason_stats.values():
            v["win_rate"] = round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0
            v["avg_pct"] = round(v["total_pct"] / v["count"], 2) if v["count"] else 0

        # 2. 국면별 수익률
        regime_stats: dict = {}
        for t in sells:
            regime = t.market_regime or "UNKNOWN"
            if regime not in regime_stats:
                regime_stats[regime] = {"count": 0, "wins": 0, "total_pct": 0.0}
            regime_stats[regime]["count"] += 1
            pct = t.profit_pct or 0.0
            regime_stats[regime]["total_pct"] += pct
            if pct > 0:
                regime_stats[regime]["wins"] += 1

        for v in regime_stats.values():
            v["win_rate"] = round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0
            v["avg_pct"] = round(v["total_pct"] / v["count"], 2) if v["count"] else 0

        # 3. 점수 구간별 승률 (매수 시점 hybrid_score)
        score_bins: dict = {}
        for t in sells:
            score = t.hybrid_score
            if score is None:
                bin_label = "no_score"
            elif score >= 80:
                bin_label = "80+"
            elif score >= 60:
                bin_label = "60-79"
            elif score >= 40:
                bin_label = "40-59"
            else:
                bin_label = "<40"

            if bin_label not in score_bins:
                score_bins[bin_label] = {"count": 0, "wins": 0, "total_pct": 0.0}
            score_bins[bin_label]["count"] += 1
            pct = t.profit_pct or 0.0
            score_bins[bin_label]["total_pct"] += pct
            if pct > 0:
                score_bins[bin_label]["wins"] += 1

        for v in score_bins.values():
            v["win_rate"] = round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0
            v["avg_pct"] = round(v["total_pct"] / v["count"], 2) if v["count"] else 0

        # 전체 요약
        total_trades = len(sells)
        total_wins = sum(1 for t in sells if (t.profit_pct or 0) > 0)
        total_pct = sum(t.profit_pct or 0 for t in sells)

        result = {
            "period_days": 30,
            "total_trades": total_trades,
            "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0,
            "avg_profit_pct": round(total_pct / total_trades, 2) if total_trades else 0,
            "by_reason": reason_stats,
            "by_regime": regime_stats,
            "by_score_bin": score_bins,
            "updated_at": datetime.now().isoformat(),
        }

        r.set("ai:performance:latest", json.dumps(result, ensure_ascii=False), ex=86400)
        return JobResult(
            count=total_trades,
            message=f"AI performance analyzed: {total_trades} trades, win_rate={result['win_rate']}%",
        )
    except Exception as e:
        logger.exception("AI performance analysis failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/analyst-feedback")
def analyst_feedback() -> JobResult:
    """분석가 피드백 — AI 성과 기반 마크다운 리포트 생성."""
    try:
        r = get_redis()

        # Redis에서 최신 AI 성과 분석 결과 조회
        raw = r.get("ai:performance:latest")
        if not raw:
            return JobResult(
                success=False, message="No AI performance data available. Run analyze-ai-performance first."
            )

        perf = json.loads(raw)

        # 마크다운 리포트 생성
        lines = [
            "# AI Trading Performance Report",
            f"**기간**: 최근 {perf.get('period_days', 30)}일",
            f"**총 거래**: {perf.get('total_trades', 0)}건",
            f"**승률**: {perf.get('win_rate', 0)}%",
            f"**평균 수익률**: {perf.get('avg_profit_pct', 0)}%",
            "",
            "## Sell Reason별 성과",
        ]

        for reason, stats in perf.get("by_reason", {}).items():
            lines.append(f"- **{reason}**: {stats['count']}건, 승률 {stats['win_rate']}%, 평균 {stats['avg_pct']}%")

        lines.append("")
        lines.append("## 국면별 수익률")
        for regime, stats in perf.get("by_regime", {}).items():
            lines.append(f"- **{regime}**: {stats['count']}건, 승률 {stats['win_rate']}%, 평균 {stats['avg_pct']}%")

        lines.append("")
        lines.append("## 점수 구간별 승률")
        for bin_label, stats in perf.get("by_score_bin", {}).items():
            lines.append(f"- **{bin_label}**: {stats['count']}건, 승률 {stats['win_rate']}%, 평균 {stats['avg_pct']}%")

        # 간단한 통계 기반 피드백
        lines.append("")
        lines.append("## 주요 피드백")

        win_rate = perf.get("win_rate", 0)
        if win_rate >= 60:
            lines.append("- 승률이 양호합니다. 현재 전략 유지를 권장합니다.")
        elif win_rate >= 40:
            lines.append("- 승률이 보통입니다. 저승률 전략의 비중 축소를 검토하세요.")
        else:
            lines.append("- 승률이 낮습니다. 진입 조건 강화 또는 스톱로스 조정이 필요합니다.")

        # 최다 손실 reason 찾기
        by_reason = perf.get("by_reason", {})
        worst_reason = min(by_reason.items(), key=lambda x: x[1].get("avg_pct", 0), default=None)
        if worst_reason and worst_reason[1].get("avg_pct", 0) < 0:
            lines.append(f"- 가장 손실이 큰 매도 사유: **{worst_reason[0]}** (평균 {worst_reason[1]['avg_pct']}%)")

        report = "\n".join(lines)

        r.set("analyst:feedback:summary", report, ex=7 * 86400)
        r.set("analyst:feedback:updated_at", datetime.now().isoformat(), ex=7 * 86400)

        return JobResult(message=f"Analyst feedback generated ({len(lines)} lines)")
    except Exception as e:
        logger.exception("Analyst feedback failed")
        return JobResult(success=False, message=str(e))


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


def _fetch_vix() -> tuple[float | None, str]:
    """Yahoo Finance에서 VIX 종가 조회. (value, regime) 반환."""
    import httpx

    try:
        resp = httpx.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        # 마지막 유효 종가
        vix = None
        for v in reversed(closes):
            if v is not None:
                vix = round(float(v), 2)
                break
        if vix is None:
            return None, "unknown"
        # Regime 분류
        if vix < 15:
            regime = "low_vol"
        elif vix < 25:
            regime = "normal"
        elif vix < 35:
            regime = "elevated"
        else:
            regime = "crisis"
        return vix, regime
    except Exception as e:
        logger.warning("VIX fetch failed: %s", e)
        return None, "unknown"


def _fetch_usd_krw(api_key: str) -> float | None:
    """BOK ECOS API에서 원/달러 환율 조회."""
    import httpx

    try:
        end = date.today()
        start = end - timedelta(days=10)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10/731Y001/D/{start_str}/{end_str}/0000001"
        )
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            return None
        # 최신 row의 DATA_VALUE
        latest = rows[-1]
        return float(latest["DATA_VALUE"].replace(",", ""))
    except Exception as e:
        logger.warning("USD/KRW fetch failed: %s", e)
        return None


@app.post("/jobs/macro-collect-global")
def macro_collect_global() -> JobResult:
    """글로벌 매크로 수집 — 직전 거래일 KOSPI/KOSDAQ + 글로벌 지표.

    pykrx는 최근 7일 범위로 조회하여 직전 거래일 데이터를 확보.
    결과를 macro:data:snapshot:{거래일} 키로 Redis에 저장 (council 호환).
    """
    try:
        from pykrx import stock as pykrx_stock

        r = get_redis()
        today = date.today()
        start_str = (today - timedelta(days=7)).strftime("%Y%m%d")
        today_str = today.strftime("%Y%m%d")

        # 직전 거래일 KOSPI/KOSDAQ 종가 수집 (7일 범위)
        snapshot: dict = {}
        trading_date = None

        for ticker, prefix in [("1001", "kospi"), ("2001", "kosdaq")]:
            try:
                df = pykrx_stock.get_index_ohlcv(start_str, today_str, ticker)
                if not df.empty:
                    last = df.iloc[-1]
                    last_date = df.index[-1]
                    if trading_date is None:
                        trading_date = last_date.date() if hasattr(last_date, "date") else last_date
                    # 전일 대비 등락률 계산
                    change_pct = 0.0
                    if len(df) >= 2:
                        prev_close = float(df.iloc[-2]["종가"])
                        if prev_close > 0:
                            change_pct = round((float(last["종가"]) - prev_close) / prev_close * 100, 2)
                    snapshot[f"{prefix}_index"] = float(last["종가"])
                    snapshot[f"{prefix}_change_pct"] = change_pct
            except Exception as e:
                logger.warning("pykrx %s collection failed: %s", prefix, e)

        if not trading_date:
            return JobResult(success=False, message="No trading data available from pykrx")

        # 외국인/기관 수급 (직전 거래일)
        td_str = str(trading_date).replace("-", "")
        for ticker, prefix in [("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")]:
            try:
                df_inv = pykrx_stock.get_market_trading_value_by_investor(
                    td_str,
                    td_str,
                    ticker,
                )
                if df_inv.empty:
                    continue
                if "외국인" in df_inv.index:
                    val = float(df_inv.loc["외국인", "순매수"]) / 1e8
                    snapshot[f"{prefix}_foreign_net"] = val
                if prefix == "kospi":
                    if "기관합계" in df_inv.index:
                        snapshot["kospi_institutional_net"] = float(df_inv.loc["기관합계", "순매수"]) / 1e8
                    if "개인" in df_inv.index:
                        snapshot["kospi_retail_net"] = float(df_inv.loc["개인", "순매수"]) / 1e8
            except Exception as e:
                logger.warning("pykrx %s investor data failed: %s", prefix, e)

        # VIX
        vix, vix_regime = _fetch_vix()
        if vix is not None:
            snapshot["vix"] = vix
            snapshot["vix_regime"] = vix_regime

        # USD/KRW
        usd_krw = None
        config = get_config()
        if config.secrets.bok_ecos_api_key:
            usd_krw = _fetch_usd_krw(config.secrets.bok_ecos_api_key)
            if usd_krw is not None:
                snapshot["usd_krw"] = usd_krw

        # 글로벌 지표 수집 (기존 스냅샷 보존)
        td_iso = trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date)
        existing_key = f"macro:data:snapshot:{td_iso}"
        existing_raw = r.get(existing_key)
        if existing_raw:
            # 기존 글로벌 지표 유지, 한국 시장 데이터만 갱신
            existing = json.loads(existing_raw)
            existing.update(snapshot)
            existing["snapshot_time"] = datetime.now().astimezone().isoformat()
            snapshot = existing
        else:
            snapshot["snapshot_date"] = td_iso
            snapshot["snapshot_time"] = datetime.now().astimezone().isoformat()
            snapshot["data_sources"] = ["pykrx"]

        r.set(existing_key, json.dumps(snapshot, ensure_ascii=False, default=str), ex=7 * 86400)

        kospi = snapshot.get("kospi_index", "?")
        kosdaq = snapshot.get("kosdaq_index", "?")
        return JobResult(
            message=(
                f"Global macro collected: trading_date={td_iso},"
                f" KOSPI={kospi}, KOSDAQ={kosdaq},"
                f" VIX={vix}, USD/KRW={usd_krw}"
            ),
        )
    except Exception as e:
        logger.exception("Global macro collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/macro-collect-korea")
def macro_collect_korea() -> JobResult:
    """국내 매크로 수집 — macro_collect_global 재사용 (한국 데이터 포함)."""
    logger.info("macro-collect-korea: delegating to macro-collect-global (includes Korean market data)")
    result = macro_collect_global()
    if result.success:
        return JobResult(message=f"Korea macro collected via global pipeline: {result.message}")
    return result


@app.post("/jobs/macro-validate-store")
def macro_validate_store() -> JobResult:
    """매크로 데이터 검증 — Redis 스냅샷 필수 필드 확인."""
    try:
        r = get_redis()
        today = date.today()

        # 최근 7일 이내 스냅샷 키 탐색
        snapshot_data = None
        for days_ago in range(7):
            check_date = today - timedelta(days=days_ago)
            key = f"macro:data:snapshot:{check_date.isoformat()}"
            raw = r.get(key)
            if raw:
                snapshot_data = json.loads(raw)
                break

        if not snapshot_data:
            return JobResult(success=False, message="No macro snapshot found in last 7 days")

        # 필수 필드 검증
        required_fields = ["kospi_index", "kosdaq_index"]
        missing = [f for f in required_fields if f not in snapshot_data or snapshot_data[f] is None]

        if missing:
            return JobResult(
                success=False,
                message=f"Macro validation failed — missing fields: {', '.join(missing)}",
            )

        # 값 범위 검증
        warnings = []
        kospi = snapshot_data.get("kospi_index", 0)
        if kospi < 1000 or kospi > 5000:
            warnings.append(f"KOSPI index unusual: {kospi}")

        kosdaq = snapshot_data.get("kosdaq_index", 0)
        if kosdaq < 300 or kosdaq > 2000:
            warnings.append(f"KOSDAQ index unusual: {kosdaq}")

        snapshot_date = snapshot_data.get("snapshot_date", "?")
        fields_present = len([k for k, v in snapshot_data.items() if v is not None and k != "data_sources"])
        msg = f"Macro validated: date={snapshot_date}, {fields_present} fields present"
        if warnings:
            msg += f" (warnings: {'; '.join(warnings)})"

        return JobResult(message=msg)
    except Exception as e:
        logger.exception("Macro validation failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/macro-quick")
def macro_quick() -> JobResult:
    """장중 매크로 빠른 업데이트."""
    return macro_collect_global()


# ─── Sync ─────────────────────────────────────────────────────


def compare_positions(
    kis_positions: list[dict],
    db_positions: list[PositionDB],
) -> dict:
    """KIS 보유 종목과 DB 포지션을 5-way 비교.

    Returns:
        {
            "only_in_kis": [{...}, ...],
            "only_in_db": [PositionDB, ...],
            "quantity_mismatch": [{"stock_code", "kis_qty", "db_qty", ...}, ...],
            "price_mismatch": [{"stock_code", "kis_avg", "db_avg", ...}, ...],
            "matched": [str, ...],
        }
    """
    kis_map: dict[str, dict] = {p["stock_code"]: p for p in kis_positions}
    db_map: dict[str, PositionDB] = {p.stock_code: p for p in db_positions}

    only_in_kis: list[dict] = []
    only_in_db: list[PositionDB] = []
    quantity_mismatch: list[dict] = []
    price_mismatch: list[dict] = []
    matched: list[str] = []

    # KIS에만 있는 종목
    for code, kis_pos in kis_map.items():
        if code not in db_map:
            only_in_kis.append(kis_pos)

    # DB에만 있는 종목
    for code, db_pos in db_map.items():
        if code not in kis_map:
            only_in_db.append(db_pos)

    # 양쪽 모두 있는 종목: 수량/가격 비교
    for code in kis_map:
        if code not in db_map:
            continue
        kis_pos = kis_map[code]
        db_pos = db_map[code]
        kis_qty = int(kis_pos.get("quantity", 0))
        db_qty = db_pos.quantity
        kis_avg = int(kis_pos.get("average_buy_price", 0))
        db_avg = db_pos.average_buy_price

        if kis_qty != db_qty:
            quantity_mismatch.append(
                {
                    "stock_code": code,
                    "stock_name": kis_pos.get("stock_name", ""),
                    "kis_qty": kis_qty,
                    "db_qty": db_qty,
                }
            )
        elif abs(kis_avg - db_avg) >= 1:
            price_mismatch.append(
                {
                    "stock_code": code,
                    "stock_name": kis_pos.get("stock_name", ""),
                    "kis_avg": kis_avg,
                    "db_avg": db_avg,
                }
            )
        else:
            matched.append(code)

    return {
        "only_in_kis": only_in_kis,
        "only_in_db": only_in_db,
        "quantity_mismatch": quantity_mismatch,
        "price_mismatch": price_mismatch,
        "matched": matched,
    }


def _ensure_stock_master(session: Session, stock_code: str, stock_name: str) -> None:
    """stock_masters에 종목이 없으면 자동 생성 (ETF·수동매수 등 FK 위반 방지)."""
    existing = session.get(StockMasterDB, stock_code)
    if existing:
        return
    session.add(
        StockMasterDB(
            stock_code=stock_code,
            stock_name=stock_name,
            market="KOSPI",
            is_active=True,
        )
    )
    session.flush()
    logger.info("stock_masters 자동 생성: %s %s", stock_code, stock_name)


def apply_sync(session: Session, diff: dict, kis_positions: list[dict], redis_client=None) -> list[str]:
    """비교 결과를 DB에 반영. KIS 데이터 기준으로 덮어쓰기."""
    actions: list[str] = []
    config = get_config()
    kis_map = {p["stock_code"]: p for p in kis_positions}

    # 1. KIS에만 있는 종목 → INSERT + BUY trade_log
    for kis_pos in diff["only_in_kis"]:
        code = kis_pos["stock_code"]
        name = kis_pos.get("stock_name", "")
        qty = int(kis_pos.get("quantity", 0))
        avg = int(kis_pos.get("average_buy_price", 0))
        total = int(kis_pos.get("total_buy_amount", 0)) or qty * avg
        cur_price = int(kis_pos.get("current_price", 0)) or avg
        sector = _resolve_sector(session, code)
        stop_loss = int(avg * (1 - config.sell.stop_loss_pct / 100))
        _ensure_stock_master(session, code, name)
        # 이전 보유 시 잔여 Redis 상태 초기화 (watermark, scale_out 등)
        if redis_client:
            _cleanup_redis_position_state(redis_client, code)
        session.add(
            PositionDB(
                stock_code=code,
                stock_name=name,
                quantity=qty,
                average_buy_price=avg,
                total_buy_amount=total,
                high_watermark=cur_price,
                stop_loss_price=stop_loss,
                sector_group=sector,
            )
        )
        session.add(
            TradeLogDB(
                stock_code=code,
                stock_name=name,
                trade_type="BUY",
                quantity=qty,
                price=avg,
                total_amount=total,
                reason="MANUAL_SYNC",
            )
        )
        actions.append(f"INSERT {code} {name} qty={qty} avg={avg:,}")

    # 2. DB에만 있는 종목 → DELETE + SELL trade_log
    for db_pos in diff["only_in_db"]:
        pos = session.get(PositionDB, db_pos.stock_code)
        if pos:
            session.add(
                TradeLogDB(
                    stock_code=db_pos.stock_code,
                    stock_name=db_pos.stock_name,
                    trade_type="SELL",
                    quantity=db_pos.quantity,
                    price=db_pos.average_buy_price,
                    total_amount=db_pos.quantity * db_pos.average_buy_price,
                    reason="MANUAL_SYNC",
                )
            )
            session.delete(pos)
            actions.append(f"DELETE {db_pos.stock_code} {db_pos.stock_name}")

    # 3. 공통 종목 → KIS 기준 덮어쓰기
    common_codes = (
        [c for c in diff.get("matched", [])]
        + [m["stock_code"] for m in diff.get("quantity_mismatch", [])]
        + [m["stock_code"] for m in diff.get("price_mismatch", [])]
    )
    for code in common_codes:
        kis_pos = kis_map.get(code)
        if not kis_pos:
            continue
        pos = session.get(PositionDB, code)
        if not pos:
            continue

        kis_qty = int(kis_pos.get("quantity", 0))
        kis_avg = int(kis_pos.get("average_buy_price", 0))
        kis_total = int(kis_pos.get("total_buy_amount", 0)) or kis_qty * kis_avg
        kis_cur = int(kis_pos.get("current_price", 0))

        changed: list[str] = []
        if pos.quantity != kis_qty:
            changed.append(f"qty:{pos.quantity}→{kis_qty}")
            pos.quantity = kis_qty
        if pos.average_buy_price != kis_avg:
            changed.append(f"avg:{pos.average_buy_price:,}→{kis_avg:,}")
            pos.average_buy_price = kis_avg
            pos.stop_loss_price = int(kis_avg * (1 - config.sell.stop_loss_pct / 100))
        pos.total_buy_amount = kis_total
        if kis_cur and (pos.high_watermark is None or kis_cur > pos.high_watermark):
            changed.append(f"hwm:{pos.high_watermark}→{kis_cur}")
            pos.high_watermark = kis_cur
        if pos.sector_group is None:
            sector = _resolve_sector(session, code)
            if sector:
                pos.sector_group = sector
                changed.append(f"sector:→{sector}")

        if changed:
            pos.updated_at = datetime.utcnow()
            actions.append(f"UPDATE {code} {kis_pos.get('stock_name', '')} {', '.join(changed)}")

    return actions


def _cleanup_redis_position_state(redis_client, stock_code: str) -> None:
    """MANUAL_SYNC 시 이전 보유의 잔여 Redis 상태 초기화.

    이전 보유 시 기록된 watermark/scale_out/rsi_sold/profit_floor이
    새 포지션에 잘못 적용되는 것을 방지.
    """
    try:
        pipe = redis_client.pipeline()
        pipe.delete(f"watermark:{stock_code}")
        pipe.delete(f"scale_out:{stock_code}")
        pipe.delete(f"rsi_sold:{stock_code}")
        pipe.delete(f"profit_floor:{stock_code}")
        pipe.execute()
        logger.info("[%s] Redis position state cleaned (MANUAL_SYNC)", stock_code)
    except Exception:
        logger.debug("[%s] Redis cleanup failed", stock_code, exc_info=True)


def _resolve_sector(session: Session, stock_code: str) -> str | None:
    """stock_masters에서 섹터 조회 → SectorGroup 변환."""
    from prime_jennie.domain.sector_taxonomy import get_sector_group

    master = session.get(StockMasterDB, stock_code)
    if master and master.sector_naver:
        return str(get_sector_group(master.sector_naver, stock_code=stock_code))
    return None


@app.post("/jobs/sync-positions")
def sync_positions(
    dry_run: bool = True,
    session: Session = Depends(get_db_session),
) -> JobResult:
    """KIS 계좌 ↔ DB 포지션 동기화.

    dry_run=True(기본): 비교 리포트만 반환.
    dry_run=False: 실제 DB 반영.
    """
    try:
        kis = _get_kis()
        balance = kis.get_balance()
        kis_positions = balance.get("positions", [])
        db_positions = list(session.exec(select(PositionDB)).all())

        diff = compare_positions(kis_positions, db_positions)

        # 리포트 생성
        lines: list[str] = []
        lines.append(f"KIS: {len(kis_positions)}종목, DB: {len(db_positions)}종목")
        lines.append(f"  matched: {len(diff['matched'])}")

        if diff["only_in_kis"]:
            lines.append(f"  only_in_kis ({len(diff['only_in_kis'])}):")
            for p in diff["only_in_kis"]:
                lines.append(f"    + {p['stock_code']} {p.get('stock_name', '')} qty={p.get('quantity', 0)}")

        if diff["only_in_db"]:
            lines.append(f"  only_in_db ({len(diff['only_in_db'])}):")
            for p in diff["only_in_db"]:
                lines.append(f"    - {p.stock_code} {p.stock_name} qty={p.quantity}")

        if diff["quantity_mismatch"]:
            lines.append(f"  quantity_mismatch ({len(diff['quantity_mismatch'])}):")
            for m in diff["quantity_mismatch"]:
                lines.append(f"    ~ {m['stock_code']} {m['stock_name']} qty: {m['db_qty']}→{m['kis_qty']}")

        if diff["price_mismatch"]:
            lines.append(f"  price_mismatch ({len(diff['price_mismatch'])}):")
            for m in diff["price_mismatch"]:
                lines.append(f"    ~ {m['stock_code']} {m['stock_name']} avg: {m['db_avg']:,}→{m['kis_avg']:,}")

        has_diff = diff["only_in_kis"] or diff["only_in_db"] or diff["quantity_mismatch"] or diff["price_mismatch"]

        if not has_diff:
            return JobResult(message="All positions matched.\n" + "\n".join(lines))

        if dry_run:
            lines.insert(0, "[DRY RUN] 변경사항 미적용")
            return JobResult(message="\n".join(lines))

        actions = apply_sync(session, diff, kis_positions, redis_client=get_redis())
        session.commit()
        lines.insert(0, f"[APPLIED] {len(actions)}건 적용 완료")
        lines.extend(["", "Actions:"] + [f"  {a}" for a in actions])
        return JobResult(count=len(actions), message="\n".join(lines))
    except Exception as e:
        logger.exception("Position sync failed")
        return JobResult(success=False, message=str(e))


# ─── Weekly Jobs ───────────────────────────────────────────────


@app.post("/jobs/cleanup-old-data")
def cleanup_old_data(session: Session = Depends(get_db_session)) -> JobResult:
    """365일 이전 데이터 정리."""
    try:
        cutoff = date.today() - timedelta(days=365)
        session.exec(
            text("DELETE FROM stock_daily_prices WHERE price_date < :cutoff"),
            params={"cutoff": cutoff},
        )
        session.commit()
        return JobResult(message=f"Cleaned up data before {cutoff}")
    except Exception as e:
        logger.exception("Cleanup failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/update-naver-sectors")
def update_naver_sectors(session: Session = Depends(get_db_session)) -> JobResult:
    """네이버 업종 분류 갱신 (sector_naver + sector_group)."""
    try:
        from prime_jennie.domain.sector_taxonomy import get_sector_group
        from prime_jennie.infra.crawlers.naver import build_naver_sector_mapping

        mapping = build_naver_sector_mapping()
        count = 0
        for code, sector in mapping.items():
            stock = session.exec(select(StockMasterDB).where(StockMasterDB.stock_code == code)).first()
            if stock:
                stock.sector_naver = sector
                stock.sector_group = get_sector_group(sector, stock_code=code).value
                count += 1

        session.commit()
        return JobResult(count=count, message=f"Updated {count} sector mappings")
    except Exception as e:
        logger.exception("Naver sector update failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/weekly-factor-analysis")
def weekly_factor_analysis(session: Session = Depends(get_db_session)) -> JobResult:
    """주간 팩터 분석 — IC(Information Coefficient) + 조건부 성과 분석."""
    try:
        import numpy as np
        import pandas as pd

        r = get_redis()
        today = date.today()
        start = today - timedelta(days=30)

        # DailyQuantScoreDB 조회 (최근 30일, final_selected)
        scores = session.exec(
            select(DailyQuantScoreDB).where(
                DailyQuantScoreDB.score_date >= start,
                DailyQuantScoreDB.is_final_selected == True,  # noqa: E712
            )
        ).all()

        if not scores:
            return JobResult(message="No quant scores in last 30 days")

        # T+5 수익률 매핑을 위한 일봉 데이터 조회
        stock_codes = list({s.stock_code for s in scores})
        prices_query = session.exec(
            select(StockDailyPriceDB).where(
                col(StockDailyPriceDB.stock_code).in_(stock_codes),
                StockDailyPriceDB.price_date >= start,
            )
        ).all()

        # stock_code → date → close_price 맵
        price_map: dict[str, dict[date, int]] = {}
        for p in prices_query:
            if p.stock_code not in price_map:
                price_map[p.stock_code] = {}
            price_map[p.stock_code][p.price_date] = p.close_price

        # IC 계산: 각 점수와 T+5 수익률의 상관계수
        factor_names = [
            "total_quant_score",
            "momentum_score",
            "quality_score",
            "value_score",
            "technical_score",
            "news_score",
            "supply_demand_score",
        ]

        rows = []
        for s in scores:
            prices = price_map.get(s.stock_code, {})
            # T+5 수익률 계산
            dates_sorted = sorted(prices.keys())
            score_idx = None
            for i, d in enumerate(dates_sorted):
                if d >= s.score_date:
                    score_idx = i
                    break
            if score_idx is None or score_idx + 5 >= len(dates_sorted):
                continue

            entry_price = prices[dates_sorted[score_idx]]
            exit_price = prices[dates_sorted[min(score_idx + 5, len(dates_sorted) - 1)]]
            if entry_price <= 0:
                continue

            fwd_return = (exit_price - entry_price) / entry_price * 100

            row = {"fwd_return_5d": fwd_return}
            for fn in factor_names:
                row[fn] = getattr(s, fn, None)
            rows.append(row)

        if len(rows) < 10:
            return JobResult(message=f"Not enough data for IC calculation ({len(rows)} samples)")

        df = pd.DataFrame(rows)

        # IC 계산 (스피어만 상관)
        ic_results: dict = {}
        for fn in factor_names:
            if fn in df.columns and df[fn].notna().sum() >= 10:
                corr = df[fn].corr(df["fwd_return_5d"], method="spearman")
                ic_results[fn] = round(float(corr) if not np.isnan(corr) else 0, 4)

        # 조건부 성과: 외국인 순매수 + 뉴스 긍정 조합
        conditional: dict = {}
        high_news = df[df["news_score"] >= 70] if "news_score" in df.columns else pd.DataFrame()
        high_supply = df[df["supply_demand_score"] >= 70] if "supply_demand_score" in df.columns else pd.DataFrame()

        if not high_news.empty:
            conditional["high_news_score"] = {
                "count": len(high_news),
                "avg_return": round(float(high_news["fwd_return_5d"].mean()), 2),
                "win_rate": round(float((high_news["fwd_return_5d"] > 0).mean() * 100), 1),
            }
        if not high_supply.empty:
            conditional["high_supply_demand"] = {
                "count": len(high_supply),
                "avg_return": round(float(high_supply["fwd_return_5d"].mean()), 2),
                "win_rate": round(float((high_supply["fwd_return_5d"] > 0).mean() * 100), 1),
            }

        # 두 조건 동시 충족
        combined = df[(df.get("news_score", 0) >= 70) & (df.get("supply_demand_score", 0) >= 70)]
        if not combined.empty:
            conditional["news_and_supply_combined"] = {
                "count": len(combined),
                "avg_return": round(float(combined["fwd_return_5d"].mean()), 2),
                "win_rate": round(float((combined["fwd_return_5d"] > 0).mean() * 100), 1),
            }

        result = {
            "sample_count": len(rows),
            "ic_spearman": ic_results,
            "conditional_performance": conditional,
            "updated_at": datetime.now().isoformat(),
        }

        r.set("factor:analysis:latest", json.dumps(result, ensure_ascii=False), ex=7 * 86400)

        top_factor = max(ic_results.items(), key=lambda x: abs(x[1]), default=("none", 0))
        return JobResult(
            count=len(rows),
            message=f"Factor analysis: {len(rows)} samples, top IC={top_factor[0]}({top_factor[1]})",
        )
    except Exception as e:
        logger.exception("Weekly factor analysis failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-consensus")
def collect_consensus(session: Session = Depends(get_db_session)) -> JobResult:
    """주간 컨센서스 수집 (FnGuide/Naver → stock_consensus UPSERT).

    활성 종목 상위 300개를 순회하며 Forward PER/EPS/ROE 수집.
    0.5초 딜레이, 100건 배치 커밋.
    """
    from prime_jennie.infra.crawlers.fnguide import crawl_consensus

    try:
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()

        today = date.today()
        updated = 0
        fnguide_ok = 0
        naver_ok = 0
        failed = 0

        for idx, stock in enumerate(stocks, 1):
            data = crawl_consensus(stock.stock_code)
            if data is not None:
                existing = session.get(StockConsensusDB, (stock.stock_code, today))
                if existing:
                    existing.forward_per = data.forward_per
                    existing.forward_eps = data.forward_eps
                    existing.forward_roe = data.forward_roe
                    existing.target_price = data.target_price
                    existing.analyst_count = data.analyst_count
                    existing.investment_opinion = data.investment_opinion
                    existing.source = data.source
                else:
                    session.add(
                        StockConsensusDB(
                            stock_code=stock.stock_code,
                            trade_date=today,
                            forward_per=data.forward_per,
                            forward_eps=data.forward_eps,
                            forward_roe=data.forward_roe,
                            target_price=data.target_price,
                            analyst_count=data.analyst_count,
                            investment_opinion=data.investment_opinion,
                            source=data.source,
                        )
                    )
                updated += 1
                if data.source == "FNGUIDE":
                    fnguide_ok += 1
                else:
                    naver_ok += 1
            else:
                failed += 1

            if idx % 100 == 0:
                session.commit()
                logger.info(
                    "Consensus collect progress: %d/%d (updated=%d)",
                    idx,
                    len(stocks),
                    updated,
                )

            time.sleep(0.5)

        session.commit()
        msg = f"Consensus collected: {updated}/{len(stocks)} (fnguide={fnguide_ok}, naver={naver_ok}, failed={failed})"
        logger.info(msg)
        return JobResult(count=updated, message=msg)
    except Exception as e:
        logger.exception("Consensus collection failed")
        return JobResult(success=False, message=str(e))


# ─── Monthly Jobs ───────────────────────────────────────────────


@app.post("/jobs/collect-naver-roe")
def collect_naver_roe(session: Session = Depends(get_db_session)) -> JobResult:
    """월간 ROE 수집 (네이버 금융 크롤링 → stock_fundamentals UPSERT).

    활성 종목 상위 300개를 순회하며 ROE를 수집.
    0.3초 딜레이, 100건 배치 커밋.
    """
    from prime_jennie.infra.crawlers.naver import crawl_naver_roe

    try:
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()

        today = date.today()
        updated = 0
        errors = 0

        for idx, stock in enumerate(stocks, 1):
            roe = crawl_naver_roe(stock.stock_code)
            if roe is not None:
                existing = session.get(StockFundamentalDB, (stock.stock_code, today))
                if existing:
                    existing.roe = roe
                    existing.updated_at = datetime.utcnow()
                else:
                    session.add(
                        StockFundamentalDB(
                            stock_code=stock.stock_code,
                            trade_date=today,
                            roe=roe,
                        )
                    )
                updated += 1
            else:
                errors += 1

            if idx % 100 == 0:
                session.commit()
                logger.info("ROE collect progress: %d/%d (updated=%d)", idx, len(stocks), updated)

            time.sleep(0.3)

        session.commit()
        logger.info("ROE collection done: %d updated, %d errors out of %d", updated, errors, len(stocks))
        return JobResult(count=updated, message=f"ROE collected: {updated}/{len(stocks)} (errors={errors})")
    except Exception as e:
        logger.exception("ROE collection failed")
        return JobResult(success=False, message=str(e))


@app.post("/jobs/collect-quarterly-financials")
def collect_quarterly_financials(session: Session = Depends(get_db_session)) -> JobResult:
    """분기 재무 수집 (네이버 금융 PER/PBR/ROE → stock_fundamentals UPSERT).

    활성 종목 상위 300개를 순회하며 최신 실적 분기 PER/PBR/ROE를 수집.
    0.5초 딜레이, 100건 배치 커밋.
    """
    from prime_jennie.infra.crawlers.naver import crawl_naver_fundamentals

    try:
        stocks = session.exec(
            select(StockMasterDB)
            .where(StockMasterDB.is_active)
            .order_by(col(StockMasterDB.market_cap).desc())
            .limit(300)
        ).all()

        today = date.today()
        updated = 0
        errors = 0

        for idx, stock in enumerate(stocks, 1):
            result = crawl_naver_fundamentals(stock.stock_code)
            if result is not None:
                existing = session.get(StockFundamentalDB, (stock.stock_code, today))
                if existing:
                    if result.per is not None:
                        existing.per = result.per
                    if result.pbr is not None:
                        existing.pbr = result.pbr
                    if result.roe is not None:
                        existing.roe = result.roe
                    existing.updated_at = datetime.utcnow()
                else:
                    session.add(
                        StockFundamentalDB(
                            stock_code=stock.stock_code,
                            trade_date=today,
                            per=result.per,
                            pbr=result.pbr,
                            roe=result.roe,
                        )
                    )
                updated += 1
            else:
                errors += 1

            if idx % 100 == 0:
                session.commit()
                logger.info(
                    "Quarterly financials progress: %d/%d (updated=%d)",
                    idx,
                    len(stocks),
                    updated,
                )

            time.sleep(0.5)

        session.commit()
        logger.info(
            "Quarterly financials done: %d updated, %d errors out of %d",
            updated,
            errors,
            len(stocks),
        )
        return JobResult(
            count=updated,
            message=f"Quarterly financials collected: {updated}/{len(stocks)} (errors={errors})",
        )
    except Exception as e:
        logger.exception("Quarterly financials collection failed")
        return JobResult(success=False, message=str(e))
