"""Unit tests — naver_stock.py 파서 로직 (HTML 목업 기반)."""

from datetime import date

from prime_jennie.infra.crawlers.naver_stock import (
    StockFrgnRow,
    _parse_float,
    _parse_int,
    _parse_signed_int,
    parse_frgn_table,
)

# ── HTML Mock ────────────────────────────────────────────────────

SAMPLE_HTML = """
<html><body>
<!-- 첫 번째 table.type2: 거래원 정보 (스킵 대상) -->
<table class="type2" summary="거래원정보에 관한표이며 일자별 누적 정보를 제공합니다.">
  <tr><th>매도상위</th><th>거래량</th><th>매수상위</th><th>거래량</th></tr>
</table>

<!-- 두 번째 table.type2: 외국인 기관 순매매 (파싱 대상) -->
<table class="type2" summary="외국인 기관 순매매 거래량에 관한표이며 날짜별로 정보를 제공합니다.">
  <tr>
    <th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th>
    <th>거래량</th><th>기관</th><th>외국인</th>
  </tr>
  <tr>
    <th colspan="5"></th><th>순매매량</th><th>순매매량</th><th>보유주수</th><th>보유율</th>
  </tr>
  <tr><td></td></tr>
  <tr>
    <td class="tc">2026.02.27</td>
    <td class="num">216,500</td>
    <td class="num">하락1,500</td>
    <td class="num">-0.69%</td>
    <td class="num">50,131,538</td>
    <td class="num">+1,554,880</td>
    <td class="num">-19,602,376</td>
    <td class="num">2,979,264,164</td>
    <td class="num">50.33%</td>
  </tr>
  <tr>
    <td class="tc">2026.02.26</td>
    <td class="num">218,000</td>
    <td class="num">상승14,500</td>
    <td class="num">+7.13%</td>
    <td class="num">30,095,763</td>
    <td class="num">+1,604,297</td>
    <td class="num">-7,738,139</td>
    <td class="num">2,998,866,540</td>
    <td class="num">50.66%</td>
  </tr>
  <tr><td></td></tr>
  <tr>
    <td class="tc">2026.02.25</td>
    <td class="num">203,500</td>
    <td class="num">상승3,500</td>
    <td class="num">+1.75%</td>
    <td class="num">26,987,996</td>
    <td class="num">+1,393,600</td>
    <td class="num">-8,781,657</td>
    <td class="num">3,007,370,145</td>
    <td class="num">50.80%</td>
  </tr>
</table>
</body></html>
"""

EMPTY_TABLE_HTML = """
<html><body>
<table class="type2" summary="외국인 기관 순매매 거래량에 관한표이며 날짜별로 정보를 제공합니다.">
  <tr><th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th><th>거래량</th><th>기관</th><th>외국인</th></tr>
  <tr><td></td></tr>
</table>
</body></html>
"""

NO_TABLE_HTML = "<html><body><p>No data</p></body></html>"


# ── Helper parse tests ───────────────────────────────────────────


class TestParseHelpers:
    def test_parse_signed_int_positive(self):
        assert _parse_signed_int("+1,554,880") == 1_554_880

    def test_parse_signed_int_negative(self):
        assert _parse_signed_int("-19,602,376") == -19_602_376

    def test_parse_signed_int_unicode_minus(self):
        assert _parse_signed_int("−500") == -500

    def test_parse_signed_int_empty(self):
        assert _parse_signed_int("") == 0
        assert _parse_signed_int("-") == 0

    def test_parse_int(self):
        assert _parse_int("216,500") == 216_500

    def test_parse_int_empty(self):
        assert _parse_int("") == 0

    def test_parse_float_percent(self):
        assert _parse_float("50.33%") == 50.33

    def test_parse_float_negative(self):
        assert _parse_float("-0.69%") == -0.69

    def test_parse_float_empty(self):
        assert _parse_float("") == 0.0


# ── Table parse tests ────────────────────────────────────────────


class TestParseFrgnTable:
    def test_normal_data(self):
        rows = parse_frgn_table(SAMPLE_HTML)
        assert len(rows) == 3

    def test_first_row_values(self):
        rows = parse_frgn_table(SAMPLE_HTML)
        r = rows[0]
        assert isinstance(r, StockFrgnRow)
        assert r.trade_date == date(2026, 2, 27)
        assert r.close_price == 216_500
        assert r.inst_net_volume == 1_554_880
        assert r.frgn_net_volume == -19_602_376
        assert r.frgn_holding_ratio == 50.33

    def test_second_row_positive_foreign(self):
        rows = parse_frgn_table(SAMPLE_HTML)
        r = rows[1]
        assert r.trade_date == date(2026, 2, 26)
        assert r.close_price == 218_000
        assert r.frgn_net_volume == -7_738_139
        assert r.frgn_holding_ratio == 50.66

    def test_empty_table(self):
        rows = parse_frgn_table(EMPTY_TABLE_HTML)
        assert rows == []

    def test_no_table(self):
        rows = parse_frgn_table(NO_TABLE_HTML)
        assert rows == []

    def test_spacer_rows_skipped(self):
        """스페이서 행(td 1개)은 무시되어야 함."""
        rows = parse_frgn_table(SAMPLE_HTML)
        # 3개 데이터 행만 파싱 (스페이서 제외)
        assert len(rows) == 3
        dates = [r.trade_date for r in rows]
        assert date(2026, 2, 27) in dates
        assert date(2026, 2, 26) in dates
        assert date(2026, 2, 25) in dates

    def test_krw_conversion(self):
        """주 수 × 종가 = KRW 금액 변환 검증."""
        rows = parse_frgn_table(SAMPLE_HTML)
        r = rows[0]
        frgn_krw = r.frgn_net_volume * r.close_price
        # -19,602,376 * 216,500 = -4,243,914,404,000
        assert frgn_krw == -19_602_376 * 216_500
