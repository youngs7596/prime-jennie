"""ATR-based Risk-Parity Position Sizing.

ATR(Average True Range)을 이용한 리스크 패리티 포지션 사이징.
1R(1 Risk) = ATR × multiplier로 정의하고, 총 자산 대비 리스크 비율로 수량 결정.
"""

import logging
from typing import Optional

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import SectorGroup, TradeTier
from prime_jennie.domain.trading import PositionSizingRequest, PositionSizingResult

logger = logging.getLogger(__name__)

# Safety Constants
MAX_POSITION_PCT_DEFAULT = 12.0
MAX_POSITION_PCT_A_PLUS = 18.0
LLM_SCORE_A_PLUS_THRESHOLD = 80.0
PORTFOLIO_HEAT_LIMIT = 5.0
SECTOR_RISK_MULTIPLIER = 0.7
MIN_QUANTITY = 1
MAX_QUANTITY = 10000
CASH_KEEP_PCT = 10.0


def get_dynamic_max_position_pct(llm_score: float) -> float:
    """LLM 점수에 따른 최대 포지션 비중."""
    if llm_score >= LLM_SCORE_A_PLUS_THRESHOLD:
        return MAX_POSITION_PCT_A_PLUS
    return MAX_POSITION_PCT_DEFAULT


def get_sector_risk_multiplier(
    sector: Optional[SectorGroup], held_sectors: list[SectorGroup]
) -> float:
    """동일 섹터 보유 시 감산 배율."""
    if sector is None or not held_sectors:
        return 1.0
    if sector in held_sectors:
        return SECTOR_RISK_MULTIPLIER
    return 1.0


def check_portfolio_heat(
    current_risk_pct: float, new_risk_pct: float
) -> bool:
    """포트폴리오 열 제한 체크. True=OK, False=초과."""
    return (current_risk_pct + new_risk_pct) <= PORTFOLIO_HEAT_LIMIT


def get_tier_multiplier(trade_tier: TradeTier) -> float:
    """티어별 포지션 배율."""
    config = get_config()
    return {
        TradeTier.TIER1: 1.0,
        TradeTier.TIER2: 0.5,
        TradeTier.BLOCKED: 0.0,
    }.get(trade_tier, 0.5)


def get_stale_multiplier(stale_days: int) -> float:
    """Stale score 감산 배율.

    0-1일: 1.0 (정상)
    2일: 0.5
    3일+: 0.3
    """
    if stale_days <= 1:
        return 1.0
    if stale_days == 2:
        return 0.5
    return 0.3


def calculate_position_size(request: PositionSizingRequest) -> PositionSizingResult:
    """ATR-based Risk-Parity 포지션 사이징.

    Algorithm:
    1. total_assets = cash + portfolio_value
    2. risk_amount = total_assets × risk_pct × sector_mult
    3. quantity = risk_amount / (ATR × atr_multiplier)
    4. Apply constraints: max_position_pct, cash_floor, min/max qty
    5. Apply tier/stale multipliers
    """
    config = get_config()
    risk_per_trade = config.risk.max_position_value_pct / 100  # % → ratio
    atr_multiplier = 2.0  # 2 ATR = 1R

    total_assets = request.available_cash + request.portfolio_value
    if total_assets <= 0:
        return PositionSizingResult(
            quantity=0,
            target_weight_pct=0,
            actual_weight_pct=0,
            applied_multipliers={},
            reasoning="No assets available",
        )

    # Sector risk multiplier
    sector_mult = get_sector_risk_multiplier(
        request.sector_group, request.held_sector_groups
    )

    # Risk amount
    effective_risk_pct = 0.01 * sector_mult  # 1% base × sector
    risk_amount = total_assets * effective_risk_pct

    # Risk per share = ATR × multiplier
    risk_per_share = request.atr * atr_multiplier
    if risk_per_share <= 0:
        return PositionSizingResult(
            quantity=0,
            target_weight_pct=0,
            actual_weight_pct=0,
            applied_multipliers={},
            reasoning="ATR is zero",
        )

    # Base quantity
    target_quantity = int(risk_amount / risk_per_share)
    if target_quantity <= 0:
        target_quantity = 1

    # Dynamic max position %
    max_pct = get_dynamic_max_position_pct(request.llm_score)
    max_position_value = total_assets * (max_pct / 100)
    max_qty_by_pct = int(max_position_value / request.stock_price) if request.stock_price > 0 else target_quantity

    # Cash floor: 투자 후 최소 현금 유지
    cash_keep = total_assets * (CASH_KEEP_PCT / 100)
    investable = max(0, request.available_cash - cash_keep)
    max_qty_by_cash = int(investable / request.stock_price) if request.stock_price > 0 else 0

    # 수량 제한 적용
    quantity = min(target_quantity, max_qty_by_pct, max_qty_by_cash, MAX_QUANTITY)
    quantity = max(quantity, 0)

    # Smart Skip: 현금 부족으로 목표의 50% 미만이면 포기
    # (max_position_pct 제한은 정상이므로 smart skip 대상 아님)
    if target_quantity > 0 and max_qty_by_cash < target_quantity * 0.5 and quantity == max_qty_by_cash:
        return PositionSizingResult(
            quantity=0,
            target_weight_pct=round(target_quantity * request.stock_price / total_assets * 100, 2),
            actual_weight_pct=0,
            applied_multipliers={"smart_skip": 0.0},
            reasoning=f"Smart skip: cash allows {max_qty_by_cash}/{target_quantity} < 50%",
        )

    # Portfolio Heat check
    actual_risk_pct = (quantity * risk_per_share / total_assets * 100) if total_assets > 0 else 0
    if not check_portfolio_heat(request.portfolio_risk_pct, actual_risk_pct):
        return PositionSizingResult(
            quantity=0,
            target_weight_pct=round(target_quantity * request.stock_price / total_assets * 100, 2),
            actual_weight_pct=0,
            applied_multipliers={"portfolio_heat": 0.0},
            reasoning=f"Portfolio heat exceeded: {request.portfolio_risk_pct + actual_risk_pct:.1f}% > {PORTFOLIO_HEAT_LIMIT}%",
        )

    # Tier multiplier
    tier_mult = get_tier_multiplier(request.trade_tier)

    # Stale multiplier
    stale_mult = get_stale_multiplier(request.stale_days)

    # Position multiplier (from macro context)
    pos_mult = request.position_multiplier

    # Final quantity
    raw_final = int(quantity * tier_mult * stale_mult * pos_mult)
    if raw_final <= 0:
        final_quantity = 0
    else:
        final_quantity = max(MIN_QUANTITY, raw_final)
    final_quantity = min(final_quantity, MAX_QUANTITY)

    # Result
    actual_pct = (
        round(final_quantity * request.stock_price / total_assets * 100, 2)
        if total_assets > 0
        else 0
    )
    target_pct = (
        round(target_quantity * request.stock_price / total_assets * 100, 2)
        if total_assets > 0
        else 0
    )

    multipliers = {
        "sector": sector_mult,
        "tier": tier_mult,
        "stale": stale_mult,
        "position": pos_mult,
    }

    parts = []
    if tier_mult < 1.0:
        parts.append(f"tier={request.trade_tier}({tier_mult}x)")
    if stale_mult < 1.0:
        parts.append(f"stale={request.stale_days}d({stale_mult}x)")
    if sector_mult < 1.0:
        parts.append(f"sector_discount({sector_mult}x)")
    reasoning = f"qty={final_quantity}, {', '.join(parts)}" if parts else f"qty={final_quantity}"

    return PositionSizingResult(
        quantity=final_quantity,
        target_weight_pct=target_pct,
        actual_weight_pct=actual_pct,
        applied_multipliers=multipliers,
        reasoning=reasoning,
    )


def calculate_atr(prices: list[dict], period: int = 14) -> float:
    """True Range 기반 ATR 계산.

    prices: [{"high": int, "low": int, "close": int}, ...]
    """
    if len(prices) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(prices)):
        high = prices[i]["high"]
        low = prices[i]["low"]
        prev_close = prices[i - 1]["close"]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # 최근 period만 사용
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def calculate_rsi(close_prices: list[float], period: int = 14) -> float | None:
    """14-period RSI 계산.

    close_prices: 시간순 종가 리스트 (oldest → newest).
    최소 period+1 개 필요. 데이터 부족 시 None.
    """
    if len(close_prices) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(close_prices)):
        delta = close_prices[i] - close_prices[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))

    # Wilder's Smoothing (EMA)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def clamp_atr(atr: float, stock_price: float) -> float:
    """ATR을 주가의 1-5% 범위로 클램프. 기본값: 2%."""
    if atr <= 0 or stock_price <= 0:
        return stock_price * 0.02

    min_atr = stock_price * 0.01
    max_atr = stock_price * 0.05
    return max(min_atr, min(atr, max_atr))
