"""Exit Rules — 다층 매도 조건 판정 엔진.

조건 우선순위 (12개):
  0.  Hard Stop (-10%, gap-down safety)
  1.  Profit Floor (15%+ 도달 후 floor 미만 → 전량)
  2.  Profit Lock (ATR 기반 동적 trigger, L1/L2)
  2.5 Breakeven Stop (+3% 도달 후 floor(+0.3%) 미만 → 전량)
  3.  ATR Stop (MACD/death cross 시 ×0.75/×0.8)
  4.  Fixed Stop (-5%, 시간 기반 tightening 적용)
  5.  Trailing Take-Profit (MACD/death cross 시 activation 축소)
  6.  Scale-Out (config 기반 + 최소 거래 가드)
  7.  RSI Overbought (50% 부분 매도)
  8.  Target Profit (trailing 비활성 시 10% 전량)
  9.  Death Cross (5MA/20MA 하향 돌파 → 전량)
 10.  Time Exit (max_holding_days=30)
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
    macd_bearish: bool = False  # MACD bearish divergence 경고
    death_cross: bool = False  # 5MA/20MA 데드크로스
    profit_floor_active: bool = False  # profit floor 활성 여부
    profit_floor_level: float = 0.0  # profit floor 수준 (%)


# --- Individual Exit Rules ---


def check_hard_stop(ctx: PositionContext) -> ExitSignal | None:
    """[0] Hard stop: -10% 이하 즉시 매도 (gap-down override)."""
    if ctx.profit_pct <= -10.0:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=f"Hard stop: {ctx.profit_pct:.1f}% <= -10%",
        )
    return None


def check_profit_floor(ctx: PositionContext) -> ExitSignal | None:
    """[1] Profit Floor: 15%+ 도달 후 floor(10%) 미만 → 전량 매도."""
    if not ctx.profit_floor_active:
        return None

    if ctx.profit_pct < ctx.profit_floor_level:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.PROFIT_FLOOR,
            quantity_pct=100.0,
            description=(f"Profit Floor: {ctx.profit_pct:.1f}% < floor {ctx.profit_floor_level:.1f}%"),
        )
    return None


def check_profit_lock(ctx: PositionContext) -> ExitSignal | None:
    """[2] Profit Lock: ATR 기반 동적 trigger.

    L2: ATR*2.5 기반 trigger (3~5%, floor 1.0%)
    L1: ATR*1.5 기반 trigger (1.5~3%, floor 0.2%)

    고점 수익률이 trigger 이상이었다가 floor 미만으로 떨어지면 전량 매도.
    """
    config = get_config().sell

    if ctx.buy_price <= 0 or ctx.atr <= 0:
        return None

    atr_pct = ctx.atr / ctx.buy_price * 100.0

    # L2: 고수익 구간 보호
    l2_trigger = max(config.profit_lock_l2_min, min(atr_pct * config.profit_lock_l2_mult, config.profit_lock_l2_max))
    if ctx.high_profit_pct >= l2_trigger and ctx.profit_pct < config.profit_lock_l2_floor:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TRAILING_STOP,
            quantity_pct=100.0,
            description=(
                f"Profit Lock L2: high={ctx.high_profit_pct:.1f}% >= trigger={l2_trigger:.1f}%"
                f" → now={ctx.profit_pct:.1f}% < floor={config.profit_lock_l2_floor}%"
            ),
        )

    # L1: 초기 수익 보호
    l1_trigger = max(config.profit_lock_l1_min, min(atr_pct * config.profit_lock_l1_mult, config.profit_lock_l1_max))
    if ctx.high_profit_pct >= l1_trigger and ctx.profit_pct < config.profit_lock_l1_floor:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.TRAILING_STOP,
            quantity_pct=100.0,
            description=(
                f"Profit Lock L1: high={ctx.high_profit_pct:.1f}% >= trigger={l1_trigger:.1f}%"
                f" → now={ctx.profit_pct:.1f}% < floor={config.profit_lock_l1_floor}%"
            ),
        )

    return None


def check_breakeven_stop(ctx: PositionContext) -> ExitSignal | None:
    """[2.5] Breakeven Stop: 한번 +X% 도달 후 floor 이하 → 전량 매도."""
    config = get_config().sell
    if not config.breakeven_enabled:
        return None
    if ctx.high_profit_pct >= config.breakeven_activation_pct and ctx.profit_pct < config.breakeven_floor_pct:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.BREAKEVEN_STOP,
            quantity_pct=100.0,
            description=(
                f"Breakeven stop: high={ctx.high_profit_pct:.1f}% >= "
                f"{config.breakeven_activation_pct}%, "
                f"now={ctx.profit_pct:.1f}% < floor={config.breakeven_floor_pct}%"
            ),
        )
    return None


def check_atr_stop(ctx: PositionContext, macro_stop_mult: float = 1.0) -> ExitSignal | None:
    """[3] ATR Trailing Stop: buy_price - ATR*mult 이하면 손절.

    MACD bearish → mult ×0.75, death_cross → mult ×0.8 (스톱 타이트닝).
    """
    config = get_config().sell
    atr_mult = config.atr_multiplier * macro_stop_mult

    if ctx.atr <= 0:
        return None

    # 경고 기반 스톱 조정
    if ctx.macd_bearish:
        atr_mult *= 0.75
    elif ctx.death_cross:
        atr_mult *= 0.8

    stop_price = ctx.buy_price - ctx.atr * atr_mult
    if ctx.current_price <= stop_price:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=(
                f"ATR stop: price {ctx.current_price:.0f} <= {stop_price:.0f} (ATR={ctx.atr:.0f}, mult={atr_mult:.2f})"
            ),
        )
    return None


def check_fixed_stop(
    ctx: PositionContext,
    macro_stop_mult: float = 1.0,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """[4] Fixed Stop Loss: 설정 비율 이하면 손절 (기본 -5%).

    Time-tightening: start_days~max_holding_days에 걸쳐 손절선을 최대 2%p 축소.
    BULL/STRONG_BULL: 15일 시작 (모멘텀 2차 상승 여유), 그 외: 10일 시작.
    """
    config = get_config()
    sell_cfg = config.sell
    threshold = -sell_cfg.stop_loss_pct * macro_stop_mult

    # 국면별 tightening 시작일
    if regime in (MarketRegime.STRONG_BULL, MarketRegime.BULL):
        start_days = sell_cfg.time_tighten_start_days_bull
    else:
        start_days = sell_cfg.time_tighten_start_days

    # 시간 기반 조임: start_days~max_holding_days에 걸쳐 최대 reduction_pct 축소
    if sell_cfg.time_tighten_enabled and ctx.holding_days > start_days:
        days_over = ctx.holding_days - start_days
        max_span = sell_cfg.max_holding_days - start_days
        if max_span > 0:
            tighten = min(
                sell_cfg.time_tighten_max_reduction_pct,
                sell_cfg.time_tighten_max_reduction_pct * days_over / max_span,
            )
            threshold += tighten  # -5% → -4% → -3% (점진적)

    if ctx.profit_pct <= threshold:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.STOP_LOSS,
            quantity_pct=100.0,
            description=f"Fixed stop: {ctx.profit_pct:.1f}% <= {threshold:.1f}% (day {ctx.holding_days})",
        )
    return None


def check_trailing_take_profit(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """[5] Trailing Take-Profit: 고점 대비 일정 비율 하락 시 전량 매도.

    MACD bearish → activation ×0.8, death_cross → activation ×0.7.
    """
    config = get_config().sell
    if not config.trailing_enabled:
        return None

    activation_pct = config.trailing_activation_pct
    # 경고 기반 activation 하향 (더 빨리 trailing 활성)
    if ctx.macd_bearish:
        activation_pct *= 0.8
    elif ctx.death_cross:
        activation_pct *= 0.7

    if ctx.high_profit_pct < activation_pct:
        return None

    # 국면별 drop threshold
    drop_pct = {
        MarketRegime.STRONG_BULL: 3.0,
        MarketRegime.BULL: 3.0,
        MarketRegime.SIDEWAYS: config.trailing_drop_from_high_pct,
        MarketRegime.BEAR: config.trailing_drop_from_high_pct,
        MarketRegime.STRONG_BEAR: 4.0,
    }.get(regime, config.trailing_drop_from_high_pct)

    min_profit = config.trailing_min_profit_pct

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


def check_scale_out(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """[6] Scale-Out: config 기반 분할 익절 + 최소 거래 가드."""
    config = get_config().sell
    if not config.scale_out_enabled:
        return None

    levels = config.get_scale_out_levels(regime)
    current_level = ctx.scale_out_level
    if current_level >= len(levels):
        return None

    target_pct, sell_pct = levels[current_level]
    if ctx.profit_pct < target_pct:
        return None

    # 최소 거래 가드: 매도 금액 or 수량이 최소 미만이면 건너뜀
    estimated_sell = max(1, int(ctx.quantity * sell_pct / 100))
    sell_amount = estimated_sell * ctx.current_price
    remaining = ctx.quantity - estimated_sell

    if sell_amount < config.min_transaction_amount or estimated_sell < config.min_sell_quantity:
        # 전량 매도로 전환할지 판단: 잔량도 최소 미만이면 전량
        total_amount = ctx.quantity * ctx.current_price
        if total_amount < config.min_transaction_amount * 2:
            sell_pct = 100.0
        else:
            return None  # 최소 거래 미달 → 스킵

    # 잔량이 너무 적으면 전량 매도
    if remaining < config.min_sell_quantity and sell_pct < 100:
        sell_pct = 100.0

    return ExitSignal(
        should_sell=True,
        reason=SellReason.PROFIT_TARGET,
        quantity_pct=sell_pct,
        description=f"Scale-out L{current_level}: profit {ctx.profit_pct:.1f}% >= {target_pct}% → sell {sell_pct:.0f}%",
    )


def check_rsi_overbought(ctx: PositionContext) -> ExitSignal | None:
    """[7] RSI 과열 부분 매도 (50%)."""
    config = get_config().sell
    if ctx.rsi_sold:
        return None
    if ctx.rsi is None:
        return None
    if ctx.rsi >= config.rsi_overbought_threshold and ctx.profit_pct >= config.rsi_min_profit_pct:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.RSI_OVERBOUGHT,
            quantity_pct=50.0,
            description=(
                f"RSI overbought: RSI={ctx.rsi:.1f} >= {config.rsi_overbought_threshold}, profit={ctx.profit_pct:.1f}%"
            ),
        )
    return None


def check_profit_target(ctx: PositionContext) -> ExitSignal | None:
    """[8] 고정 이익 목표 도달 시 전량 매도 (trailing 비활성일 때 폴백)."""
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


def check_death_cross(ctx: PositionContext) -> ExitSignal | None:
    """[9] Death Cross: 5MA/20MA 하향 돌파 확인 시 전량 매도.

    death_cross 판정은 monitor/app.py에서 indicators를 통해 수행.
    여기서는 ctx.death_cross 플래그만 확인.
    """
    if ctx.death_cross and ctx.profit_pct < 0:
        return ExitSignal(
            should_sell=True,
            reason=SellReason.DEATH_CROSS,
            quantity_pct=100.0,
            description=f"Death Cross: 5MA/20MA 하향 돌파, profit={ctx.profit_pct:.1f}%",
        )
    return None


def check_time_exit(
    ctx: PositionContext,
    regime: MarketRegime = MarketRegime.SIDEWAYS,
) -> ExitSignal | None:
    """[10] 최대 보유 기간 초과 시 전량 매도."""
    config = get_config().sell
    max_days = config.max_holding_days

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
        lambda: check_profit_floor(ctx),
        lambda: check_profit_lock(ctx),
        lambda: check_breakeven_stop(ctx),
        lambda: check_atr_stop(ctx, macro_stop_mult),
        lambda: check_fixed_stop(ctx, macro_stop_mult, regime),
        lambda: check_trailing_take_profit(ctx, regime),
        lambda: check_scale_out(ctx, regime),
        lambda: check_rsi_overbought(ctx),
        lambda: check_profit_target(ctx),
        lambda: check_death_cross(ctx),
        lambda: check_time_exit(ctx, regime),
    ]

    for check in checks:
        signal = check()
        if signal and signal.should_sell:
            return signal

    return None
