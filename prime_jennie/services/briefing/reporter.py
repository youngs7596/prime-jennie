"""DailyReporter — 데이터 수집 + LLM 요약 + 텔레그램 발송.

포트폴리오 상태, 매매 내역, 워치리스트, 매크로 인사이트를 수집하여
LLM 요약 후 텔레그램 채널로 전송.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.models import (
    DailyAssetSnapshotDB,
    StockNewsSentimentDB,
    TradeLogDB,
)
from prime_jennie.infra.database.repositories import (
    AssetSnapshotRepository,
    MacroRepository,
    PortfolioRepository,
    WatchlistRepository,
)

logger = logging.getLogger(__name__)


class DailyReporter:
    """일일 브리핑 생성기."""

    def __init__(self):
        self._config = get_config()
        self._telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    async def create_and_send_report(self, session: Session) -> dict:
        """리포트 생성 + 텔레그램 발송."""
        data = self.collect_report_data(session)
        summary = self.format_report(data)

        # LLM 요약 (선택적)
        llm_summary = await self._generate_llm_summary(summary)
        final_report = llm_summary or summary

        # 텔레그램 발송
        sent = self._send_telegram(final_report)
        return {"sent": sent, "data_items": len(data)}

    def collect_report_data(self, session: Session) -> dict:
        """리포트용 데이터 수집."""
        today = date.today()

        # 포트폴리오 현황
        positions = PortfolioRepository.get_positions(session)
        position_data = [
            {
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "quantity": p.quantity,
                "avg_price": p.average_buy_price,
                "total_buy": p.total_buy_amount,
            }
            for p in positions
        ]

        # 오늘 매매 내역
        trades = PortfolioRepository.get_recent_trades(session, days=1)
        trade_data = [
            {
                "stock_code": t.stock_code,
                "stock_name": t.stock_name,
                "trade_type": t.trade_type,
                "quantity": t.quantity,
                "price": t.price,
                "reason": t.reason,
                "profit_pct": t.profit_pct,
            }
            for t in trades
        ]

        # 매크로 인사이트
        insight = MacroRepository.get_latest_insight(session)
        macro_data = None
        if insight:
            macro_data = {
                "sentiment": insight.sentiment,
                "sentiment_score": insight.sentiment_score,
                "regime_hint": insight.regime_hint,
                "sectors_to_favor": insight.sectors_to_favor,
                "sectors_to_avoid": insight.sectors_to_avoid,
            }

        # 워치리스트 Top 10
        watchlist = WatchlistRepository.get_latest(session)[:10]
        watchlist_data = [
            {
                "stock_code": w.stock_code,
                "stock_name": w.stock_name,
                "hybrid_score": w.hybrid_score,
                "trade_tier": w.trade_tier,
                "rank": w.rank,
            }
            for w in watchlist
        ]

        # 자산 스냅샷
        latest_snapshot = AssetSnapshotRepository.get_latest(session)
        asset_data = None
        if latest_snapshot:
            asset_data = {
                "total_asset": latest_snapshot.total_asset,
                "cash_balance": latest_snapshot.cash_balance,
                "stock_eval": latest_snapshot.stock_eval_amount,
                "position_count": latest_snapshot.position_count,
            }

        # 최근 뉴스 감성 Top 5
        from sqlalchemy import desc
        from sqlmodel import select

        news_stmt = (
            select(StockNewsSentimentDB)
            .where(StockNewsSentimentDB.news_date >= today - timedelta(days=1))
            .order_by(desc(StockNewsSentimentDB.sentiment_score))
            .limit(5)
        )
        news_items = list(session.exec(news_stmt).all())
        news_data = [
            {
                "stock_code": n.stock_code,
                "headline": n.headline[:50],
                "score": n.sentiment_score,
            }
            for n in news_items
        ]

        return {
            "date": str(today),
            "positions": position_data,
            "trades": trade_data,
            "macro": macro_data,
            "watchlist": watchlist_data,
            "assets": asset_data,
            "news": news_data,
        }

    def format_report(self, data: dict) -> str:
        """데이터를 텍스트 리포트로 포맷."""
        lines = [f"[Daily Briefing] {data['date']}", ""]

        # 자산 현황
        if data.get("assets"):
            a = data["assets"]
            lines.append(f"총자산: {a['total_asset']:,}원")
            lines.append(f"현금: {a['cash_balance']:,}원 | 주식: {a['stock_eval']:,}원")
            lines.append(f"보유 종목: {a['position_count']}개")
            lines.append("")

        # 매매 내역
        if data.get("trades"):
            lines.append(f"[매매] 오늘 {len(data['trades'])}건")
            for t in data["trades"][:5]:
                pnl = f" ({t['profit_pct']:+.1f}%)" if t.get("profit_pct") else ""
                lines.append(f"  {t['trade_type']} {t['stock_name']} {t['quantity']}주 @{t['price']:,}{pnl}")
            lines.append("")

        # 보유 종목
        if data.get("positions"):
            lines.append(f"[보유] {len(data['positions'])}종목")
            for p in data["positions"][:8]:
                lines.append(f"  {p['stock_name']} {p['quantity']}주 @{p['avg_price']:,}")
            lines.append("")

        # 매크로
        if data.get("macro"):
            m = data["macro"]
            lines.append(f"[매크로] {m['sentiment']} (점수: {m['sentiment_score']})")
            lines.append(f"  Regime: {m['regime_hint']}")
            lines.append("")

        # 워치리스트
        if data.get("watchlist"):
            lines.append(f"[워치리스트] Top {len(data['watchlist'])}")
            for w in data["watchlist"][:5]:
                lines.append(f"  #{w['rank']} {w['stock_name']} ({w['hybrid_score']:.0f}점, {w['trade_tier']})")
            lines.append("")

        return "\n".join(lines)

    async def _generate_llm_summary(self, report_text: str) -> str | None:
        """LLM 요약 생성 (선택적)."""
        try:
            from prime_jennie.infra.llm.factory import LLMFactory

            provider = LLMFactory.get_provider("fast")
            response = await provider.generate(
                prompt=(
                    "다음 일일 트레이딩 리포트를 읽고 핵심 포인트를 3줄로 요약하세요.\n\n"
                    f"{report_text}\n\n"
                    "요약:"
                ),
                service="briefing",
            )
            if response and response.content:
                return f"{report_text}\n\n[AI 요약]\n{response.content}"
        except Exception:
            logger.debug("LLM summary generation skipped")

        return None

    def _send_telegram(self, message: str) -> bool:
        """텔레그램 메시지 발송."""
        if not self._telegram_token or not self._telegram_chat_id:
            logger.warning("Telegram config missing, skipping send")
            return False

        try:
            url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
            resp = httpx.post(
                url,
                json={
                    "chat_id": self._telegram_chat_id,
                    "text": message[:4096],
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False
