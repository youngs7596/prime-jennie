"""Exit Rules — 다층 매도 조건 판정 엔진.

조건 우선순위:
  1. Hard Stop (gap-down safety override)
  2. Profit Lock (breakeven protection)
  3. Stop Loss (ATR trailing / fixed)
  4. Trailing Take-Profit
  5. Scale-Out (partial profit-taking)
  6. RSI Overbought (partial sell)
  7. Time Exit (max holding days)
"""

import logging
from dataclasses import dataclass

from prime_jennie.domain.config import get_config
from prime_jennie.domain.enums import MarketRegime, SellReason

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """매도 조건 판정 결과."""

    should_sell: bool = False
    reason: SellReason = SellReason.MANUAL
    quantity_pct: float = 100.0  # 0-100: 매도 비율 (%)
    description: str = ""


@dataclass
class PositionContext:
    """포지션 평가에 필요한 컨텍스트."""

    stock_code: str
    current_price: float
    buy_price: float
    quantity: int
    profit_pct: float
    high_watermark: float  # 보유 중 최고가
    high_profit_pct: float  # 최고 수익률
    atr: float
    rsi: float | None = None
    holding_days: int = 0
    scale_out_level: int = 0  # 현재 스케일아웃 단계 (0-4)
    rsi_sold: bool = False  # RSI 매도 이미 실행 여부


# --- Individual Exit Rules ---


def check_hard_stop(ctx: PositionContext) -> ExitSignal | None:
    """Hard stop: -10% 이하 즉시 매도 (gap-down override)."""
    if ctx.profit_pct <= -10.0:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=f"Hard stop: {ctx.profit_pct:.1f}% <= -10%",
        )
    return None


def check_profit_lock(ctx: PositionContext) -> ExitSignal | None:
    """Profit Lock: 고점 도달 후 이익 보존.

    Level 2: 고점 3%+ 도달 → 현재 1% 미만 → 전량 매도
    Level 1: 고점 1.5%+ 도달 → 현재 0.5% 미만 → 전량 매도
    """
    if ctx.high_profit_pct >= 3.0 and ctx.profit_pct < 1.0:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TRAILING_STOP,
            quantity_pct=100.0,
            description=f"Profit Lock L2: high={ctx.high_profit_pct:.1f}% → now={ctx.profit_pct:.1f}%",
        )
    if ctx.high_profit_pct >= 1.5 and ctx.profit_pct < 0.5:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TRAILING_STOP,
            quantity_pct=100.0,
            description=f"Profit Lock L1: high={ctx.high_profit_pct:.1f}% → now={ctx.profit_pct:.1f}%",
        )
    return None


def check_atr_stop(ctx: PositionContext, macro_stop_mult: float = 1.0) -> ExitSignal | None:
    """ATR Trailing Stop: buy_price - ATR*mult 이하면 손절."""
    get_config()
    atr_mult = 2.0 * macro_stop_mult

    if ctx.atr <= 0:
        return None

    stop_price = ctx.buy_price - ctx.atr * atr_mult
    if ctx.current_price <= stop_price:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=(
                f"ATR stop: price {ctx.current_price:.0f} <= {stop_price:.0f} (ATR={ctx.atr:.0f}, mult={atr_mult:.1f})"
            ),
        )
    return None


def check_fixed_stop(ctx: PositionContext, macro_stop_mult: float = 1.0) -> ExitSignal | None:
    """Fixed Stop Loss: 설정 비율 이하면 손절."""
    config = get_config()
    threshold = -config.sell.stop_loss_pct * macro_stop_mult
    if ctx.profit_pct <= threshold:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=f"Fixed stop: {ctx.profit_pct:.1f}% <= {threshold:.1f}%",
        )
    return None


def check_trailing_take_profit(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """Trailing Take-Profit: 고점 대비 일정 비율 하락 시 전량 매도."""
    config = get_config()
    if not config.sell.trailing_enabled:
        return None

    activation_pct = config.sell.trailing_activation_pct
    if ctx.high_profit_pct < activation_pct:
        return None

    # 국면별 drop threshold
    drop_pct = {
        MarketRegime.STRONG_BULL: 3.0,
        MarketRegime.BULL: 3.0,
        MarketRegime.SIDEWAYS: 3.5,
        MarketRegime.BEAR: 3.5,
        MarketRegime.STRONG_BEAR: 4.0,
    }.get(regime, 3.5)

    # 최소 이익 보장
    min_profit = 3.0

    trailing_stop = ctx.high_watermark * (1 - drop_pct / 100)
    if ctx.current_price <= trailing_stop and ctx.profit_pct >= min_profit:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TRAILING_STOP,
            quantity_pct=100.0,
            description=(
                f"Trailing TP: price {ctx.current_price:.0f} <= "
                f"{trailing_stop:.0f} (high={ctx.high_watermark:.0f}, drop={drop_pct}%)"
            ),
        )
    return None


def check_profit_target(ctx: PositionContext) -> ExitSignal | None:
    """고정 이익 목표 도달 시 전량 매도 (trailing 비활성일 때 폴백)."""
    config = get_config()
    if config.sell.trailing_enabled:
        return None  # trailing이 대체

    if ctx.profit_pct >= config.sell.profit_target_pct:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.PROFIT_TARGET,
            quantity_pct=100.0,
            description=f"Profit target: {ctx.profit_pct:.1f}% >= {config.sell.profit_target_pct}%",
        )
    return None


def check_scale_out(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """Scale-Out: 국면별 분할 익절.

    Returns ExitSignal with quantity_pct for partial sell.
    Level 0-3, each 25%. Level 4 = remaining (~15%).
    """
    # 국면별 이익 목표
    levels = {
        MarketRegime.STRONG_BULL: [3.0, 7.0, 15.0, 25.0],
        MarketRegime.BULL: [3.0, 7.0, 15.0, 25.0],
        MarketRegime.SIDEWAYS: [3.0, 7.0, 12.0, 18.0],
        MarketRegime.BEAR: [2.0, 5.0, 8.0, 12.0],
        MarketRegime.STRONG_BEAR: [2.0, 5.0, 8.0, 12.0],
    }.get(regime, [3.0, 7.0, 12.0, 18.0])

    current_level = ctx.scale_out_level
    if current_level >= len(levels):
        return None

    target = levels[current_level]
    if ctx.profit_pct >= target:
        pct = 25.0 if current_level < 3 else 15.0

        # 잔량이 너무 적으면 전량 매도 (최소 거래 단위 보장)
        estimated_sell = int(ctx.quantity * pct / 100)
        remaining = ctx.quantity - estimated_sell
        if remaining < 10:
            pct = 100.0

        return ExitSignal(
            should_sell=True,
            reason=SellReason.PROFIT_TARGET,
            quantity_pct=pct,
            description=f"Scale-out L{current_level}: profit {ctx.profit_pct:.1f}% >= {target}% → sell {pct:.0f}%",
        )
    return None


def check_rsi_overbought(ctx: PositionContext) -> ExitSignal | None:
    """RSI 과열 부분 매도 (50%)."""
    config = get_config()
    if ctx.rsi_sold:
        return None
    if ctx.rsi is None:
        return None
    if ctx.rsi >= config.sell.rsi_overbought_threshold and ctx.profit_pct >= 3.0:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.RSI_OVERBOUGHT,
            quantity_pct=50.0,
            description=(
                f"RSI overbought: RSI={ctx.rsi:.1f} >= {config.sell.rsi_overbought_threshold},"
                f" profit={ctx.profit_pct:.1f}%"
            ),
        )
    return None


def check_time_exit(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """최대 보유 기간 초과 시 전량 매도."""
    config = get_config()
    max_days = {
        MarketRegime.STRONG_BULL: config.sell.time_exit_bull_days,
        MarketRegime.BULL: config.sell.time_exit_bull_days,
    }.get(regime, config.sell.time_exit_sideways_days)

    if ctx.holding_days >= max_days:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TIME_EXIT,
            quantity_pct=100.0,
            description=f"Time exit: {ctx.holding_days}d >= {max_days}d ({regime})",
        )
    return None


# --- Orchestrator ---


def evaluate_exit(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
    macro_stop_mult: float = 1.0,
) -> ExitSignal | None:
    """모든 매도 조건을 우선순위대로 평가. 첫 번째 매치 반환."""
    checks = [
        lambda: check_hard_stop(ctx),
        lambda: check_profit_lock(ctx),
        lambda: check_atr_stop(ctx, macro_stop_mult),
        lambda: check_fixed_stop(ctx, macro_stop_mult),
        lambda: check_trailing_take_profit(ctx, regime),
        lambda: check_profit_target(ctx),
        lambda: check_scale_out(ctx, regime),
        lambda: check_rsi_overbought(ctx),
        lambda: check_time_exit(ctx, regime),
    ]

    for check in checks:
        signal = check()
        if signal and signal.should_sell:
            return signal

    return None
