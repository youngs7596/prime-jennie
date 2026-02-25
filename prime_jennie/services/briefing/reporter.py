"""DailyReporter — 데이터 수집 + LLM 리포트 생성 + 텔레그램 발송.

포트폴리오 상태, 매매 내역, 워치리스트, 매크로 인사이트, 뉴스를 수집하여
LLM(Jennie 페르소나)이 전체 리포트를 생성하고 텔레그램 채널로 전송.
"""

import html
import json
import logging
from datetime import date, timedelta

import httpx
from sqlmodel import Session

from prime_jennie.domain.config import get_config
from prime_jennie.infra.database.models import (
    StockNewsSentimentDB,
)
from prime_jennie.infra.database.repositories import (
    AssetSnapshotRepository,
    MacroRepository,
    PortfolioRepository,
    WatchlistRepository,
)

logger = logging.getLogger(__name__)

JENNIE_SYSTEM_PROMPT = """\
당신은 사용자의 똑똑하고 다정한 주식 투자 파트너 '제니(Jennie)'입니다.
아래 데이터를 바탕으로 사용자가 퇴근길에 읽기 좋은 일일 브리핑을 작성해주세요.

[톤앤매너]
- 다정하고 따뜻한 어조 ("~했어요", "~보입니다" 등)
- 전문성은 유지하되 딱딱하지 않게
- 사용자를 격려하는 멘트를 자연스럽게 포함

[포맷 규칙 — Telegram HTML]
- 반드시 HTML 태그만 사용: <b>, <i>, <code>, <a href="...">
- Markdown(#, *, **, ```) 절대 사용 금지
- 섹션 구분은 빈 줄 + <b>섹션 제목</b> 형태
- 숫자에는 천 단위 쉼표 사용
- 전체 길이 3500자 이내

[구조]
<b>[날짜] 제니의 일일 브리핑</b>

1. <b>시장 현황</b> — 코스피/코스닥 지수, VIX, 환율, 전체 분위기
2. <b>오늘의 매매</b> — 매수/매도 건수, 수익률, 주요 매매 내역
3. <b>포트폴리오 현황</b> — 총자산, 현금비중, 보유종목
4. <b>주목할 종목</b> — 워치리스트 상위 종목
5. <b>뉴스 & 이슈</b> — 주요 뉴스 (데이터 없으면 생략)
6. <b>제니의 한마디</b> — 내일 전략 + 따뜻한 마무리

[주의사항]
- 없는 내용을 지어내지 마세요. 데이터가 없으면 솔직히 "데이터를 불러오지 못했어요"라고 적어주세요
- 긍정적이고 희망적인 에너지를 전달하세요
- HTML 특수문자(&, <, >)는 이미 이스케이프 되어있으니 그대로 사용하세요
"""


def _safe(value: object) -> str:
    """HTML 특수문자 이스케이프."""
    return html.escape(str(value))


def _parse_json_field(raw: str | None) -> list | dict | None:
    """JSON 문자열을 파싱, 실패 시 None 반환."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


class DailyReporter:
    """일일 브리핑 생성기."""

    def __init__(self):
        self._config = get_config()
        self._telegram_token = self._config.telegram.bot_token
        self._telegram_chat_id = self._config.telegram.chat_ids

    async def create_and_send_report(self, session: Session) -> dict:
        """리포트 생성 + 텔레그램 발송."""
        data = self.collect_report_data(session)
        context = self._build_data_context(data)

        # LLM 리포트 생성 시도, 실패 시 fallback HTML
        report = await self._generate_llm_report(context)
        if not report:
            report = self._format_fallback_html(data)

        sent = self._send_telegram(report)
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

        # 오늘 매매 내역 (days=0 → 오늘만)
        trades = PortfolioRepository.get_recent_trades(session, days=0)
        trade_data = [
            {
                "stock_code": t.stock_code,
                "stock_name": t.stock_name,
                "trade_type": t.trade_type,
                "quantity": t.quantity,
                "price": t.price,
                "total_amount": t.total_amount,
                "reason": t.reason,
                "profit_pct": t.profit_pct,
                "profit_amount": t.profit_amount,
            }
            for t in trades
        ]

        # 매매 요약 통계
        trade_summary = self._compute_trade_summary(trade_data)

        # 매크로 인사이트 (확장 필드 포함)
        insight = MacroRepository.get_latest_insight(session)
        macro_data = None
        if insight:
            macro_data = {
                "sentiment": insight.sentiment,
                "sentiment_score": insight.sentiment_score,
                "regime_hint": insight.regime_hint,
                "kospi_index": insight.kospi_index,
                "kospi_change_pct": insight.kospi_change_pct,
                "kosdaq_index": insight.kosdaq_index,
                "kosdaq_change_pct": insight.kosdaq_change_pct,
                "vix_value": insight.vix_value,
                "vix_regime": insight.vix_regime,
                "usd_krw": insight.usd_krw,
                "council_consensus": insight.council_consensus,
                "risk_factors": _parse_json_field(insight.risk_factors_json),
                "key_themes": _parse_json_field(insight.key_themes_json),
                "trading_reasoning": insight.trading_reasoning,
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
                "headline": n.headline[:80],
                "score": n.sentiment_score,
            }
            for n in news_items
        ]

        return {
            "date": str(today),
            "positions": position_data,
            "trades": trade_data,
            "trade_summary": trade_summary,
            "macro": macro_data,
            "watchlist": watchlist_data,
            "assets": asset_data,
            "news": news_data,
        }

    @staticmethod
    def _compute_trade_summary(trade_data: list[dict]) -> dict:
        """매매 요약 통계 계산."""
        buys = [t for t in trade_data if t["trade_type"] == "BUY"]
        sells = [t for t in trade_data if t["trade_type"] == "SELL"]

        wins = [s for s in sells if (s.get("profit_pct") or 0) > 0]
        losses = [s for s in sells if (s.get("profit_pct") or 0) < 0]
        total_realized_pnl = sum(s.get("profit_amount") or 0 for s in sells)

        best_trade = None
        worst_trade = None
        if sells:
            sells_with_pct = [s for s in sells if s.get("profit_pct") is not None]
            if sells_with_pct:
                best_trade = max(sells_with_pct, key=lambda s: s["profit_pct"])
                worst_trade = min(sells_with_pct, key=lambda s: s["profit_pct"])

        sell_count = len(sells)
        win_rate = (len(wins) / sell_count * 100) if sell_count > 0 else 0.0

        return {
            "buy_count": len(buys),
            "sell_count": sell_count,
            "total_realized_pnl": total_realized_pnl,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": win_rate,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }

    def _build_data_context(self, data: dict) -> str:
        """수집 데이터를 LLM 입력용 구조화 텍스트로 변환."""
        lines: list[str] = []
        lines.append(f"[일일 브리핑 데이터] {data['date']}")
        lines.append("")

        # 자산 현황
        lines.append("== 자산 현황 ==")
        if data.get("assets"):
            a = data["assets"]
            lines.append(f"총자산: {a['total_asset']:,}원")
            lines.append(f"현금: {a['cash_balance']:,}원")
            lines.append(f"주식평가: {a['stock_eval']:,}원")
            lines.append(f"보유종목수: {a['position_count']}개")
        else:
            lines.append("자산 데이터 없음")
        lines.append("")

        # 매매 요약
        lines.append("== 오늘 매매 요약 ==")
        ts = data.get("trade_summary", {})
        buy_count = ts.get("buy_count", 0)
        sell_count = ts.get("sell_count", 0)
        if buy_count + sell_count > 0:
            lines.append(f"매수: {buy_count}건 / 매도: {sell_count}건")
            if sell_count > 0:
                lines.append(f"실현손익: {ts['total_realized_pnl']:,}원")
                lines.append(f"승률: {ts['win_rate']:.0f}% (익절 {ts['win_count']}건 / 손절 {ts['loss_count']}건)")
                if ts.get("best_trade"):
                    bt = ts["best_trade"]
                    lines.append(f"최고수익: {bt['stock_name']} {bt['profit_pct']:+.1f}%")
                if ts.get("worst_trade"):
                    wt = ts["worst_trade"]
                    lines.append(f"최저수익: {wt['stock_name']} {wt['profit_pct']:+.1f}%")
        else:
            lines.append("오늘 매매 없음")
        lines.append("")

        # 매매 상세
        if data.get("trades"):
            lines.append("== 매매 상세 ==")
            for t in data["trades"]:
                pnl = f" ({t['profit_pct']:+.1f}%)" if t.get("profit_pct") else ""
                lines.append(
                    f"{t['trade_type']} {t['stock_name']} {t['quantity']}주 @{t['price']:,}원{pnl} [{t['reason']}]"
                )
            lines.append("")

        # 보유 종목
        if data.get("positions"):
            lines.append(f"== 보유 종목 ({len(data['positions'])}개) ==")
            for p in data["positions"]:
                lines.append(f"{p['stock_name']}({p['stock_code']}) {p['quantity']}주 @{p['avg_price']:,}원")
            lines.append("")

        # 매크로/시장 지표
        lines.append("== 시장 현황 ==")
        if data.get("macro"):
            m = data["macro"]
            if m.get("kospi_index"):
                change = f" ({m['kospi_change_pct']:+.2f}%)" if m.get("kospi_change_pct") is not None else ""
                lines.append(f"코스피: {m['kospi_index']:,.2f}{change}")
            if m.get("kosdaq_index"):
                change = f" ({m['kosdaq_change_pct']:+.2f}%)" if m.get("kosdaq_change_pct") is not None else ""
                lines.append(f"코스닥: {m['kosdaq_index']:,.2f}{change}")
            if m.get("vix_value"):
                regime = f" [{m['vix_regime']}]" if m.get("vix_regime") else ""
                lines.append(f"VIX: {m['vix_value']:.2f}{regime}")
            if m.get("usd_krw"):
                lines.append(f"USD/KRW: {m['usd_krw']:,.1f}")
            lines.append(f"심리: {m['sentiment']} (점수: {m['sentiment_score']})")
            lines.append(f"국면: {m['regime_hint']}")
            if m.get("council_consensus"):
                lines.append(f"AI 의회 합의: {m['council_consensus']}")
            if m.get("trading_reasoning"):
                lines.append(f"매매 근거: {m['trading_reasoning']}")
            if m.get("sectors_to_favor"):
                lines.append(f"유망 섹터: {m['sectors_to_favor']}")
            if m.get("sectors_to_avoid"):
                lines.append(f"회피 섹터: {m['sectors_to_avoid']}")
            if m.get("key_themes"):
                themes = m["key_themes"]
                if isinstance(themes, list):
                    lines.append(f"핵심 테마: {', '.join(str(t) for t in themes)}")
            if m.get("risk_factors"):
                factors = m["risk_factors"]
                if isinstance(factors, list):
                    lines.append(f"위험 요인: {', '.join(str(f) for f in factors)}")
        else:
            lines.append("매크로 데이터 없음")
        lines.append("")

        # 워치리스트
        if data.get("watchlist"):
            lines.append(f"== 워치리스트 Top {len(data['watchlist'])} ==")
            for w in data["watchlist"]:
                rank = w["rank"] if w["rank"] is not None else "-"
                score = f"{w['hybrid_score']:.0f}" if w["hybrid_score"] is not None else "-"
                tier = w["trade_tier"] or "-"
                lines.append(f"#{rank} {w['stock_name']} ({score}점, {tier})")
            lines.append("")

        # 뉴스
        if data.get("news"):
            lines.append("== 주요 뉴스 ==")
            for n in data["news"]:
                lines.append(f"[{n['stock_code']}] {n['headline']} (감성: {n['score']})")
            lines.append("")

        return "\n".join(lines)

    async def _generate_llm_report(self, context: str) -> str | None:
        """LLM으로 Jennie 페르소나 리포트 생성."""
        try:
            from prime_jennie.infra.llm.factory import LLMFactory

            provider = LLMFactory.get_provider("fast")
            response = await provider.generate(
                prompt=context,
                system=JENNIE_SYSTEM_PROMPT,
                max_tokens=3000,
                service="briefing",
            )
            if response and response.content:
                return response.content
        except Exception:
            logger.debug("LLM report generation failed, using fallback")

        return None

    def _format_fallback_html(self, data: dict) -> str:
        """LLM 실패 시 사용할 HTML 포맷 리포트."""
        lines: list[str] = []
        lines.append(f"<b>[{_safe(data['date'])}] 일일 브리핑</b>")
        lines.append("")

        # 자산 현황
        if data.get("assets"):
            a = data["assets"]
            lines.append("<b>자산 현황</b>")
            lines.append(f"총자산: <b>{a['total_asset']:,}원</b>")
            lines.append(f"현금: {a['cash_balance']:,}원 | 주식: {a['stock_eval']:,}원")
            lines.append(f"보유 종목: {a['position_count']}개")
            lines.append("")

        # 매매 요약
        ts = data.get("trade_summary", {})
        buy_count = ts.get("buy_count", 0)
        sell_count = ts.get("sell_count", 0)
        if buy_count + sell_count > 0:
            lines.append(f"<b>오늘 매매</b> (매수 {buy_count}건 / 매도 {sell_count}건)")
            if sell_count > 0:
                pnl = ts["total_realized_pnl"]
                pnl_sign = "+" if pnl >= 0 else ""
                lines.append(f"실현손익: <b>{pnl_sign}{pnl:,}원</b> | 승률: {ts['win_rate']:.0f}%")
            for t in data.get("trades", [])[:8]:
                pnl_str = f" ({t['profit_pct']:+.1f}%)" if t.get("profit_pct") else ""
                lines.append(
                    f"  {_safe(t['trade_type'])} {_safe(t['stock_name'])} {t['quantity']}주 @{t['price']:,}{pnl_str}"
                )
            lines.append("")
        else:
            lines.append("<b>오늘 매매</b>")
            lines.append("매매 없음")
            lines.append("")

        # 보유 종목
        if data.get("positions"):
            lines.append(f"<b>보유 종목</b> ({len(data['positions'])}개)")
            for p in data["positions"][:8]:
                lines.append(f"  {_safe(p['stock_name'])} {p['quantity']}주 @{p['avg_price']:,}")
            lines.append("")

        # 시장 현황
        if data.get("macro"):
            m = data["macro"]
            lines.append("<b>시장 현황</b>")
            parts: list[str] = []
            if m.get("kospi_index"):
                change = f" ({m['kospi_change_pct']:+.2f}%)" if m.get("kospi_change_pct") is not None else ""
                parts.append(f"코스피: {m['kospi_index']:,.2f}{change}")
            if m.get("kosdaq_index"):
                change = f" ({m['kosdaq_change_pct']:+.2f}%)" if m.get("kosdaq_change_pct") is not None else ""
                parts.append(f"코스닥: {m['kosdaq_index']:,.2f}{change}")
            if m.get("vix_value"):
                parts.append(f"VIX: {m['vix_value']:.2f}")
            if m.get("usd_krw"):
                parts.append(f"USD/KRW: {m['usd_krw']:,.1f}")
            if parts:
                lines.append(" | ".join(parts))
            lines.append(
                f"심리: {_safe(m['sentiment'])} (점수: {m['sentiment_score']}) | 국면: {_safe(m['regime_hint'])}"
            )
            lines.append("")

        # 워치리스트
        if data.get("watchlist"):
            lines.append(f"<b>워치리스트</b> Top {len(data['watchlist'])}")
            for w in data["watchlist"][:5]:
                rank = w["rank"] if w["rank"] is not None else "-"
                score = f"{w['hybrid_score']:.0f}" if w["hybrid_score"] is not None else "-"
                tier = w["trade_tier"] or "-"
                lines.append(f"  #{rank} {_safe(w['stock_name'])} ({score}점, {tier})")
            lines.append("")

        # 뉴스
        if data.get("news"):
            lines.append("<b>뉴스</b>")
            for n in data["news"]:
                lines.append(f"  [{_safe(n['stock_code'])}] {_safe(n['headline'])}")
            lines.append("")

        return "\n".join(lines)

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
