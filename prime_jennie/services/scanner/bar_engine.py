"""Bar Aggregator + VWAP Engine.

실시간 틱 데이터를 1분 캔들로 집계하고 VWAP을 계산.
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Bar(BaseModel):
    """1분 캔들스틱."""

    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class BarEngine:
    """실시간 틱 → 1분 캔들 + VWAP 엔진.

    Usage:
        engine = BarEngine(bar_interval=60)
        completed = engine.update("005930", 72100, volume=50000)
        if completed:
            # 새 바 완성
            ...
        vwap = engine.get_vwap("005930")
        bars = engine.get_recent_bars("005930", count=20)
    """

    def __init__(self, bar_interval: int = 60, max_history: int = 60):
        self._interval = bar_interval
        self._max_history = max_history
        self._lock = threading.Lock()

        # 종목별 현재 진행 중인 바
        self._current_bars: dict[str, dict] = {}
        # 종목별 완성된 바 히스토리
        self._completed_bars: dict[str, list[Bar]] = defaultdict(list)
        # VWAP 누적 {code: {cum_pv, cum_vol, vwap, date}}
        self._vwap: dict[str, dict] = defaultdict(
            lambda: {"cum_pv": 0.0, "cum_vol": 0, "vwap": 0.0, "date": None}
        )
        # 거래량 히스토리 (바별 volume)
        self._volume_history: dict[str, list[int]] = defaultdict(list)

    def update(
        self, stock_code: str, price: float, volume: int = 0
    ) -> Optional[Bar]:
        """틱 수신 → 바 갱신. 바가 완성되면 반환."""
        now = datetime.now(timezone.utc).timestamp()
        bar_ts = int(now // self._interval) * self._interval

        with self._lock:
            # VWAP 업데이트 (일별 리셋)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            vwap_data = self._vwap[stock_code]
            if vwap_data["date"] != today:
                vwap_data.update(cum_pv=0.0, cum_vol=0, vwap=0.0, date=today)
            if volume > 0:
                vwap_data["cum_pv"] += price * volume
                vwap_data["cum_vol"] += volume
                vwap_data["vwap"] = (
                    vwap_data["cum_pv"] / vwap_data["cum_vol"]
                    if vwap_data["cum_vol"] > 0
                    else price
                )

            current = self._current_bars.get(stock_code)

            # 새 바 시작
            if current is None or current["ts"] != bar_ts:
                completed_bar = None
                if current is not None:
                    # 이전 바 완성
                    completed_bar = Bar(
                        timestamp=current["ts"],
                        open=current["open"],
                        high=current["high"],
                        low=current["low"],
                        close=current["close"],
                        volume=current["volume"],
                    )
                    self._completed_bars[stock_code].append(completed_bar)
                    self._volume_history[stock_code].append(current["volume"])

                    # 히스토리 제한
                    if len(self._completed_bars[stock_code]) > self._max_history:
                        self._completed_bars[stock_code] = self._completed_bars[
                            stock_code
                        ][-self._max_history :]
                    if len(self._volume_history[stock_code]) > self._max_history:
                        self._volume_history[stock_code] = self._volume_history[
                            stock_code
                        ][-self._max_history :]

                # 새 바 초기화
                self._current_bars[stock_code] = {
                    "ts": bar_ts,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": volume,
                }
                return completed_bar

            # 기존 바 갱신
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += volume
            return None

    def get_vwap(self, stock_code: str) -> float:
        """현재 VWAP 반환. 데이터 없으면 0."""
        return self._vwap[stock_code]["vwap"]

    def get_volume_info(self, stock_code: str) -> dict:
        """거래량 정보: current, avg, ratio."""
        with self._lock:
            current = self._current_bars.get(stock_code)
            current_vol = current["volume"] if current else 0
            history = self._volume_history.get(stock_code, [])
            avg_vol = sum(history) / len(history) if history else 0
            ratio = current_vol / avg_vol if avg_vol > 0 else 0.0
            return {"current": current_vol, "avg": avg_vol, "ratio": ratio}

    def get_recent_bars(self, stock_code: str, count: int = 20) -> list[Bar]:
        """최근 N개 완성 바 반환."""
        with self._lock:
            bars = self._completed_bars.get(stock_code, [])
            return bars[-count:]

    def get_current_price(self, stock_code: str) -> Optional[float]:
        """현재 바의 종가 (최신 틱). 없으면 None."""
        current = self._current_bars.get(stock_code)
        return current["close"] if current else None

    def bar_count(self, stock_code: str) -> int:
        """완성된 바 개수."""
        return len(self._completed_bars.get(stock_code, []))
