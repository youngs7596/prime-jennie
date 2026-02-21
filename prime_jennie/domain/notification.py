"""트레이드 알림 모델 — 매수/매도 체결 알림용."""

from datetime import datetime

from pydantic import BaseModel


class TradeNotification(BaseModel):
    """매수/매도 체결 알림 (Redis Stream 메시지)."""

    trade_type: str  # "BUY" | "SELL"
    stock_code: str
    stock_name: str
    quantity: int
    price: int
    total_amount: int
    # 매수 전용
    signal_type: str | None = None
    trade_tier: str | None = None
    hybrid_score: float | None = None
    # 매도 전용
    sell_reason: str | None = None
    profit_pct: float | None = None
    holding_days: int | None = None
    timestamp: datetime
