"""KIS ↔ DB 포지션 동기화 비교 로직 단위 테스트."""

from unittest.mock import MagicMock

from sqlmodel import Session

from prime_jennie.infra.database.models import PositionDB, StockMasterDB, TradeLogDB
from prime_jennie.services.jobs.app import apply_sync, compare_positions

# ─── Fixtures ─────────────────────────────────────────────────


def _kis_pos(
    stock_code: str = "005930",
    stock_name: str = "삼성전자",
    quantity: int = 100,
    average_buy_price: int = 72000,
    total_buy_amount: int = 7_200_000,
    current_price: int = 73000,
) -> dict:
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "quantity": quantity,
        "average_buy_price": average_buy_price,
        "total_buy_amount": total_buy_amount,
        "current_price": current_price,
    }


def _db_pos(
    stock_code: str = "005930",
    stock_name: str = "삼성전자",
    quantity: int = 100,
    average_buy_price: int = 72000,
    total_buy_amount: int = 7_200_000,
    sector_group: str | None = "IT",
    high_watermark: int | None = 75000,
    stop_loss_price: int | None = 68400,
) -> PositionDB:
    return PositionDB(
        stock_code=stock_code,
        stock_name=stock_name,
        quantity=quantity,
        average_buy_price=average_buy_price,
        total_buy_amount=total_buy_amount,
        sector_group=sector_group,
        high_watermark=high_watermark,
        stop_loss_price=stop_loss_price,
    )


# ─── compare_positions tests ─────────────────────────────────


class TestComparePositions:
    def test_all_matched(self):
        kis = [_kis_pos()]
        db = [_db_pos()]
        diff = compare_positions(kis, db)

        assert diff["matched"] == ["005930"]
        assert diff["only_in_kis"] == []
        assert diff["only_in_db"] == []
        assert diff["quantity_mismatch"] == []
        assert diff["price_mismatch"] == []

    def test_empty_both(self):
        diff = compare_positions([], [])
        assert diff["matched"] == []
        assert diff["only_in_kis"] == []
        assert diff["only_in_db"] == []

    def test_only_in_kis(self):
        kis = [_kis_pos(stock_code="005930"), _kis_pos(stock_code="000660", stock_name="SK하이닉스")]
        db = [_db_pos(stock_code="005930")]
        diff = compare_positions(kis, db)

        assert len(diff["only_in_kis"]) == 1
        assert diff["only_in_kis"][0]["stock_code"] == "000660"
        assert diff["matched"] == ["005930"]

    def test_only_in_db(self):
        kis = [_kis_pos(stock_code="005930")]
        db = [_db_pos(stock_code="005930"), _db_pos(stock_code="035420", stock_name="NAVER")]
        diff = compare_positions(kis, db)

        assert len(diff["only_in_db"]) == 1
        assert diff["only_in_db"][0].stock_code == "035420"

    def test_quantity_mismatch(self):
        kis = [_kis_pos(quantity=150)]
        db = [_db_pos(quantity=100)]
        diff = compare_positions(kis, db)

        assert len(diff["quantity_mismatch"]) == 1
        m = diff["quantity_mismatch"][0]
        assert m["kis_qty"] == 150
        assert m["db_qty"] == 100
        assert diff["matched"] == []

    def test_price_mismatch(self):
        kis = [_kis_pos(average_buy_price=72500)]
        db = [_db_pos(average_buy_price=72000)]
        diff = compare_positions(kis, db)

        assert len(diff["price_mismatch"]) == 1
        m = diff["price_mismatch"][0]
        assert m["kis_avg"] == 72500
        assert m["db_avg"] == 72000
        assert diff["matched"] == []

    def test_price_within_tolerance(self):
        """평단가 차이 < 1원이면 일치로 처리."""
        kis = [_kis_pos(average_buy_price=72000)]
        db = [_db_pos(average_buy_price=72000)]
        diff = compare_positions(kis, db)

        assert diff["matched"] == ["005930"]
        assert diff["price_mismatch"] == []

    def test_quantity_takes_priority_over_price(self):
        """수량과 가격 모두 다르면 quantity_mismatch로 분류."""
        kis = [_kis_pos(quantity=150, average_buy_price=73000)]
        db = [_db_pos(quantity=100, average_buy_price=72000)]
        diff = compare_positions(kis, db)

        assert len(diff["quantity_mismatch"]) == 1
        assert diff["price_mismatch"] == []

    def test_mixed_all_categories(self):
        """5가지 카테고리가 모두 등장하는 케이스."""
        kis = [
            _kis_pos(stock_code="005930", quantity=100, average_buy_price=72000),  # matched
            _kis_pos(stock_code="000660", stock_name="SK하이닉스"),  # only_in_kis
            _kis_pos(stock_code="035720", stock_name="카카오", quantity=200),  # qty mismatch
            _kis_pos(  # price mismatch
                stock_code="006400", stock_name="삼성SDI", quantity=50, average_buy_price=400000
            ),
        ]
        db = [
            _db_pos(stock_code="005930", quantity=100, average_buy_price=72000),  # matched
            _db_pos(stock_code="035420", stock_name="NAVER"),  # only_in_db
            _db_pos(stock_code="035720", stock_name="카카오", quantity=150),  # qty mismatch
            _db_pos(stock_code="006400", stock_name="삼성SDI", quantity=50, average_buy_price=395000),  # price mismatch
        ]
        diff = compare_positions(kis, db)

        assert "005930" in diff["matched"]
        assert len(diff["only_in_kis"]) == 1
        assert diff["only_in_kis"][0]["stock_code"] == "000660"
        assert len(diff["only_in_db"]) == 1
        assert diff["only_in_db"][0].stock_code == "035420"
        assert len(diff["quantity_mismatch"]) == 1
        assert diff["quantity_mismatch"][0]["stock_code"] == "035720"
        assert len(diff["price_mismatch"]) == 1
        assert diff["price_mismatch"][0]["stock_code"] == "006400"


# ─── apply_sync tests ────────────────────────────────────────


class TestApplySync:
    def _mock_session(self, existing: dict[str, PositionDB] | None = None) -> MagicMock:
        session = MagicMock(spec=Session)
        store = dict(existing or {})

        def mock_get(model, pk):
            return store.get(pk)

        session.get.side_effect = mock_get
        return session

    def test_insert_only_in_kis(self):
        session = self._mock_session()
        kis = [_kis_pos(stock_code="000660", stock_name="SK하이닉스", current_price=180000)]
        diff = {
            "only_in_kis": kis,
            "only_in_db": [],
            "quantity_mismatch": [],
            "price_mismatch": [],
            "matched": [],
        }
        actions = apply_sync(session, diff, kis)

        assert len(actions) == 1
        assert "INSERT" in actions[0]
        assert "000660" in actions[0]
        # StockMasterDB 자동 생성 + PositionDB INSERT + TradeLogDB BUY = 3회 호출
        assert session.add.call_count == 3
        added_master = session.add.call_args_list[0][0][0]
        added_pos = session.add.call_args_list[1][0][0]
        added_log = session.add.call_args_list[2][0][0]
        assert isinstance(added_master, StockMasterDB)
        assert added_master.stock_code == "000660"
        assert isinstance(added_pos, PositionDB)
        assert added_pos.stock_code == "000660"
        assert added_pos.high_watermark == 180000
        assert added_pos.stop_loss_price is not None
        assert isinstance(added_log, TradeLogDB)
        assert added_log.trade_type == "BUY"
        assert added_log.reason == "MANUAL_SYNC"

    def test_insert_fallback_high_watermark(self):
        """current_price가 0이면 average_buy_price를 high_watermark로 사용."""
        session = self._mock_session()
        kis = [_kis_pos(stock_code="000660", current_price=0, average_buy_price=170000)]
        diff = {
            "only_in_kis": kis,
            "only_in_db": [],
            "quantity_mismatch": [],
            "price_mismatch": [],
            "matched": [],
        }
        apply_sync(session, diff, kis)
        added_pos = session.add.call_args_list[1][0][0]
        assert added_pos.high_watermark == 170000

    def test_delete_only_in_db(self):
        pos = _db_pos(stock_code="035420", stock_name="NAVER")
        session = self._mock_session(existing={"035420": pos})
        diff = {
            "only_in_kis": [],
            "only_in_db": [pos],
            "quantity_mismatch": [],
            "price_mismatch": [],
            "matched": [],
        }
        actions = apply_sync(session, diff, [])

        assert len(actions) == 1
        assert "DELETE" in actions[0]
        session.delete.assert_called_once_with(pos)
        # TradeLogDB SELL 기록
        added_log = session.add.call_args[0][0]
        assert isinstance(added_log, TradeLogDB)
        assert added_log.trade_type == "SELL"
        assert added_log.reason == "MANUAL_SYNC"

    def test_overwrite_quantity_from_kis(self):
        """수량 불일치 시 KIS 기준으로 덮어쓰기."""
        pos = _db_pos(stock_code="005930", quantity=100, average_buy_price=72000)
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", quantity=150, average_buy_price=72000, total_buy_amount=10_800_000)]
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [{"stock_code": "005930", "stock_name": "삼성전자", "kis_qty": 150, "db_qty": 100}],
            "price_mismatch": [],
            "matched": [],
        }
        actions = apply_sync(session, diff, kis)

        assert len(actions) == 1
        assert "qty:100→150" in actions[0]
        assert pos.quantity == 150
        assert pos.total_buy_amount == 10_800_000

    def test_overwrite_price_from_kis(self):
        """평균단가 불일치 시 KIS 기준으로 덮어쓰기 + stop_loss 재계산."""
        pos = _db_pos(stock_code="005930", quantity=100, average_buy_price=72000, stop_loss_price=68400)
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", quantity=100, average_buy_price=72500, total_buy_amount=7_250_000)]
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [],
            "price_mismatch": [{"stock_code": "005930", "stock_name": "삼성전자", "kis_avg": 72500, "db_avg": 72000}],
            "matched": [],
        }
        actions = apply_sync(session, diff, kis)

        assert len(actions) == 1
        assert "avg:72,000→72,500" in actions[0]
        assert pos.average_buy_price == 72500
        assert pos.total_buy_amount == 7_250_000
        # stop_loss 재계산됨
        assert pos.stop_loss_price != 68400

    def test_overwrite_both_qty_and_price(self):
        """수량+가격 모두 다를 때 둘 다 KIS 기준으로 덮어쓰기."""
        pos = _db_pos(stock_code="005930", quantity=100, average_buy_price=72000)
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", quantity=150, average_buy_price=73000, total_buy_amount=10_950_000)]
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [{"stock_code": "005930", "stock_name": "삼성전자", "kis_qty": 150, "db_qty": 100}],
            "price_mismatch": [],
            "matched": [],
        }
        actions = apply_sync(session, diff, kis)

        assert len(actions) == 1
        assert pos.quantity == 150
        assert pos.average_buy_price == 73000

    def test_preserves_sector_group(self):
        """UPDATE 시 기존 sector_group 보존."""
        pos = _db_pos(
            stock_code="005930",
            quantity=100,
            average_buy_price=72000,
            sector_group="IT",
            high_watermark=75000,
        )
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", quantity=150, current_price=73000)]
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [{"stock_code": "005930", "stock_name": "삼성전자", "kis_qty": 150, "db_qty": 100}],
            "price_mismatch": [],
            "matched": [],
        }
        apply_sync(session, diff, kis)

        assert pos.sector_group == "IT"
        assert pos.high_watermark == 75000  # 73000 < 75000 이므로 유지

    def test_updates_high_watermark_when_higher(self):
        """KIS current_price가 기존 watermark보다 높으면 갱신."""
        pos = _db_pos(stock_code="005930", high_watermark=73000)
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", current_price=76000)]
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [],
            "price_mismatch": [],
            "matched": ["005930"],
        }
        actions = apply_sync(session, diff, kis)

        assert pos.high_watermark == 76000
        assert len(actions) == 1
        assert "hwm:" in actions[0]

    def test_matched_no_changes(self):
        """완전 일치 시 변경 없음."""
        pos = _db_pos(stock_code="005930", high_watermark=75000)
        session = self._mock_session(existing={"005930": pos})
        kis = [_kis_pos(stock_code="005930", current_price=73000)]  # 73000 < 75000
        diff = {
            "only_in_kis": [],
            "only_in_db": [],
            "quantity_mismatch": [],
            "price_mismatch": [],
            "matched": ["005930"],
        }
        actions = apply_sync(session, diff, kis)

        assert actions == []
        session.add.assert_not_called()
        session.delete.assert_not_called()
