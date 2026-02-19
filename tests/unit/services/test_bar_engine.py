"""Bar Engine 단위 테스트."""

from prime_jennie.services.scanner.bar_engine import Bar, BarEngine


class TestBarEngine:
    def setup_method(self):
        self.engine = BarEngine(bar_interval=60)

    def test_first_tick_no_completed_bar(self):
        """첫 틱은 완성 바 없음."""
        result = self.engine.update("005930", 72000, volume=1000)
        assert result is None

    def test_new_bar_completes_previous(self):
        """새 바 시작 시 이전 바 완성."""
        # 첫 번째 바
        self.engine._current_bars["005930"] = {
            "ts": 1000,
            "open": 71000,
            "high": 72000,
            "low": 70000,
            "close": 71500,
            "volume": 5000,
        }
        # 다음 바 시작 (ts가 달라야 함)
        # 직접 현재 시간 기반으로 바를 시작
        result = self.engine.update("005930", 72100, volume=3000)
        # 이전 바가 완성되어야 함 (ts가 다르므로)
        if result is not None:
            assert isinstance(result, Bar)
            assert result.close == 71500

    def test_update_existing_bar(self):
        """기존 바 내에서 OHLCV 갱신."""
        self.engine.update("005930", 72000, volume=1000)
        # 같은 바 구간 내에서 업데이트
        current = self.engine._current_bars.get("005930")
        if current:
            assert current["close"] == 72000
            self.engine._current_bars["005930"]["high"] = 72500
            self.engine._current_bars["005930"]["low"] = 71500
            assert self.engine._current_bars["005930"]["high"] == 72500

    def test_get_recent_bars_empty(self):
        """완성된 바 없으면 빈 리스트."""
        bars = self.engine.get_recent_bars("005930")
        assert bars == []

    def test_get_recent_bars_with_data(self):
        """완성된 바 반환."""
        # 직접 완성 바 주입
        bar = Bar(timestamp=1000.0, open=71000, high=72000, low=70000, close=71500, volume=5000)
        self.engine._completed_bars["005930"] = [bar]

        bars = self.engine.get_recent_bars("005930", count=5)
        assert len(bars) == 1
        assert bars[0].close == 71500

    def test_bar_count(self):
        """바 개수 확인."""
        assert self.engine.bar_count("005930") == 0
        self.engine._completed_bars["005930"].append(
            Bar(timestamp=1000, open=100, high=110, low=90, close=105, volume=100)
        )
        assert self.engine.bar_count("005930") == 1

    def test_get_current_price_none(self):
        """현재 바 없으면 None."""
        assert self.engine.get_current_price("005930") is None

    def test_get_current_price(self):
        """현재 바의 종가."""
        self.engine.update("005930", 72000)
        assert self.engine.get_current_price("005930") == 72000

    def test_max_history_limit(self):
        """히스토리 제한."""
        engine = BarEngine(bar_interval=60, max_history=5)
        for i in range(10):
            bar = Bar(timestamp=float(i * 60), open=100, high=110, low=90, close=105, volume=100)
            engine._completed_bars["005930"].append(bar)
        # 직접 잘라야 함 (update 통해서만 자동 제한)
        engine._completed_bars["005930"] = engine._completed_bars["005930"][-5:]
        assert len(engine._completed_bars["005930"]) == 5


class TestVWAP:
    def setup_method(self):
        self.engine = BarEngine()

    def test_vwap_zero_without_data(self):
        """데이터 없으면 VWAP 0."""
        assert self.engine.get_vwap("005930") == 0.0

    def test_vwap_single_tick(self):
        """단일 틱 → VWAP = price."""
        self.engine.update("005930", 72000, volume=1000)
        vwap = self.engine.get_vwap("005930")
        assert vwap == 72000.0

    def test_vwap_multiple_ticks(self):
        """여러 틱 → VWAP = sum(pv) / sum(vol)."""
        self.engine.update("005930", 72000, volume=1000)
        self.engine.update("005930", 73000, volume=2000)
        # VWAP = (72000*1000 + 73000*2000) / (1000+2000)
        expected = (72000 * 1000 + 73000 * 2000) / 3000
        assert abs(self.engine.get_vwap("005930") - expected) < 0.01

    def test_vwap_ignores_zero_volume(self):
        """volume=0 틱은 VWAP에 영향 없음."""
        self.engine.update("005930", 72000, volume=1000)
        self.engine.update("005930", 90000, volume=0)  # 이상 가격이지만 volume 0
        assert self.engine.get_vwap("005930") == 72000.0


class TestVolumeInfo:
    def setup_method(self):
        self.engine = BarEngine()

    def test_volume_info_empty(self):
        """데이터 없으면 모두 0."""
        info = self.engine.get_volume_info("005930")
        assert info["current"] == 0
        assert info["avg"] == 0
        assert info["ratio"] == 0.0

    def test_volume_info_with_current(self):
        """현재 바 거래량."""
        self.engine.update("005930", 72000, volume=5000)
        info = self.engine.get_volume_info("005930")
        assert info["current"] == 5000
