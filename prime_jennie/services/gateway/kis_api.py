"""KIS (한국투자증권) REST API 직접 호출 래퍼.

실제 KIS OpenAPI와 통신. 토큰 관리, 헤더 구성, 에러 핸들링 포함.
Gateway 서비스(app.py)가 이 모듈을 사용하여 KIS API를 프록시.

Reference: https://apiportal.koreainvestment.com/
"""

import json
import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from prime_jennie.domain.config import KISConfig, get_config
from prime_jennie.domain.stock import DailyPrice, StockSnapshot

logger = logging.getLogger(__name__)


class KISApiError(Exception):
    """KIS API 오류."""

    def __init__(self, message: str, rt_cd: str = "", msg_cd: str = ""):
        super().__init__(message)
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd


class KISApi:
    """KIS OpenAPI 직접 호출 클라이언트.

    토큰 발급/갱신, 공통 헤더 구성, 에러 핸들링 포함.
    """

    def __init__(self, config: KISConfig | None = None):
        self._config = config or get_config().kis
        self._client = httpx.Client(
            base_url=self._config.base_url,
            timeout=30.0,
        )
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._load_cached_token()

    # ─── Authentication ──────────────────────────────────────────

    def authenticate(self) -> str:
        """접근 토큰 발급/갱신. 캐싱된 유효 토큰이 있으면 재사용."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        resp = self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._config.app_key,
                "appsecret": self._config.app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        self._access_token = data["access_token"]
        # KIS 토큰은 보통 24시간 유효
        self._token_expires_at = time.time() + data.get("expires_in", 86400)
        self._save_cached_token()

        logger.info("KIS token refreshed, expires in %ds", data.get("expires_in", 86400))
        return self._access_token

    def _load_cached_token(self) -> None:
        """파일에서 캐싱된 토큰 로드."""
        path = Path(self._config.token_file_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            if time.time() < data.get("expires_at", 0) - 60:
                self._access_token = data["access_token"]
                self._token_expires_at = data["expires_at"]
                logger.debug("Loaded cached KIS token")
        except (json.JSONDecodeError, KeyError):
            pass

    def _save_cached_token(self) -> None:
        """토큰을 파일에 캐싱."""
        path = Path(self._config.token_file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "access_token": self._access_token,
                        "expires_at": self._token_expires_at,
                    }
                )
            )
        except OSError as e:
            logger.warning("Failed to cache KIS token: %s", e)

    # ─── Common Request ──────────────────────────────────────────

    def _headers(self, tr_id: str) -> dict[str, str]:
        """KIS API 공통 헤더 구성."""
        token = self.authenticate()
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """KIS API 공통 요청."""
        headers = self._headers(tr_id)

        resp = self._client.request(method, path, headers=headers, params=params, json=json_data)
        resp.raise_for_status()
        data = resp.json()

        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg = data.get("msg1", "Unknown KIS error")
            raise KISApiError(msg, rt_cd=rt_cd, msg_cd=data.get("msg_cd", ""))

        return data

    # ─── Market Data ─────────────────────────────────────────────

    def get_snapshot(self, stock_code: str) -> StockSnapshot:
        """현재가 조회 (FHKST01010100)."""
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
            },
        )
        output = data.get("output", {})

        return StockSnapshot(
            stock_code=stock_code,
            price=int(output.get("stck_prpr", 0)),
            open_price=int(output.get("stck_oprc", 0)),
            high_price=int(output.get("stck_hgpr", 0)),
            low_price=int(output.get("stck_lwpr", 0)),
            volume=int(output.get("acml_vol", 0)),
            change_pct=float(output.get("prdy_ctrt", 0)),
            per=_safe_float(output.get("per")),
            pbr=_safe_float(output.get("pbr")),
            market_cap=_safe_int(output.get("hts_avls")),
            high_52w=_safe_int(output.get("stck_dryy_hgpr")),
            low_52w=_safe_int(output.get("stck_dryy_lwpr")),
            timestamp=datetime.now(UTC),
        )

    def get_daily_prices(self, stock_code: str, days: int = 150) -> list[DailyPrice]:
        """일봉 조회 (FHKST01010400)."""
        end_date = date.today().strftime("%Y%m%d")
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            tr_id="FHKST01010400",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )

        prices = []
        for row in data.get("output", [])[:days]:
            try:
                prices.append(
                    DailyPrice(
                        stock_code=stock_code,
                        price_date=datetime.strptime(row["stck_bsop_date"], "%Y%m%d").date(),
                        open_price=int(row.get("stck_oprc", 0)),
                        high_price=int(row.get("stck_hgpr", 0)),
                        low_price=int(row.get("stck_lwpr", 0)),
                        close_price=int(row.get("stck_clpr", 0)),
                        volume=int(row.get("acml_vol", 0)),
                        change_pct=_safe_float(row.get("prdy_ctrt")),
                    )
                )
            except (KeyError, ValueError) as e:
                logger.warning("Skipping malformed daily price row: %s", e)
                continue

        return prices

    def get_minute_prices(self, stock_code: str, minutes: int = 5) -> list[dict]:
        """분봉 조회 (FHKST03010200).

        Args:
            stock_code: 종목코드
            minutes: 분 단위 (1, 3, 5, 10, 15, 30, 60)
        """
        from prime_jennie.domain.stock import MinutePrice

        now = datetime.now()
        time_str = now.strftime("%H%M%S")

        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            tr_id="FHKST03010200",
            params={
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_HOUR_1": time_str,
                "FID_PW_DATA_INCU_YN": "N",
            },
        )

        prices = []
        for row in data.get("output2", []):
            try:
                dt_str = row.get("stck_bsop_date", now.strftime("%Y%m%d"))
                tm_str = row.get("stck_cntg_hour", "000000")
                price_dt = datetime.strptime(f"{dt_str}{tm_str}", "%Y%m%d%H%M%S")

                prices.append(
                    MinutePrice(
                        stock_code=stock_code,
                        price_datetime=price_dt,
                        open_price=int(row.get("stck_oprc", 0)),
                        high_price=int(row.get("stck_hgpr", 0)),
                        low_price=int(row.get("stck_lwpr", 0)),
                        close_price=int(row.get("stck_prpr", 0)),
                        volume=int(row.get("cntg_vol", 0)),
                    )
                )
            except (KeyError, ValueError) as e:
                logger.warning("Skipping malformed minute price row: %s", e)
                continue

        return prices

    # ─── Trading ─────────────────────────────────────────────────

    def place_order(
        self,
        *,
        order_type: str,
        stock_code: str,
        quantity: int,
        price: int = 0,
    ) -> dict[str, Any]:
        """주문 실행 (매수: TTTC0802U, 매도: TTTC0801U).

        Args:
            order_type: "buy" or "sell"
            stock_code: 종목코드
            quantity: 수량
            price: 가격 (0이면 시장가)
        """
        tr_id = "TTTC0802U" if order_type == "buy" else "TTTC0801U"
        if self._config.is_paper:
            tr_id = "VTTC0802U" if order_type == "buy" else "VTTC0801U"

        # 시장가: ORD_DVSN=01, 지정가: ORD_DVSN=00
        ord_dvsn = "01" if price == 0 else "00"

        data = self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id=tr_id,
            json_data={
                "CANO": self._config.account_no,
                "ACNT_PRDT_CD": self._config.account_product_code,
                "PDNO": stock_code,
                "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(quantity),
                "ORD_UNPR": str(price),
            },
        )

        output = data.get("output", {})
        return {
            "order_no": output.get("ODNO", ""),
            "order_time": output.get("ORD_TMD", ""),
        }

    def cancel_order(self, order_no: str) -> bool:
        """주문 취소 (TTTC0803U)."""
        tr_id = "TTTC0803U"
        if self._config.is_paper:
            tr_id = "VTTC0803U"

        try:
            self._request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                tr_id=tr_id,
                json_data={
                    "CANO": self._config.account_no,
                    "ACNT_PRDT_CD": self._config.account_product_code,
                    "KRX_FWDG_ORD_ORGNO": "",
                    "ORGN_ODNO": order_no,
                    "ORD_DVSN": "00",
                    "RVSE_CNCL_DVSN_CD": "02",  # 취소
                    "ORD_QTY": "0",
                    "ORD_UNPR": "0",
                    "QTY_ALL_ORD_YN": "Y",
                },
            )
            return True
        except KISApiError as e:
            logger.error("Cancel order failed: %s", e)
            return False

    # ─── Account ─────────────────────────────────────────────────

    def get_balance(self) -> dict[str, Any]:
        """잔고 조회 (TTTC8434R)."""
        tr_id = "TTTC8434R"
        if self._config.is_paper:
            tr_id = "VTTC8434R"

        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": self._config.account_no,
                "ACNT_PRDT_CD": self._config.account_product_code,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

        positions = []
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            positions.append(
                {
                    "stock_code": item.get("pdno", ""),
                    "stock_name": item.get("prdt_name", ""),
                    "quantity": qty,
                    "average_buy_price": int(float(item.get("pchs_avg_pric", 0))),
                    "total_buy_amount": int(item.get("pchs_amt", 0)),
                    "current_price": int(item.get("prpr", 0)),
                    "current_value": int(item.get("evlu_amt", 0)),
                    "profit_pct": float(item.get("evlu_pfls_rt", 0)),
                }
            )

        output2 = data.get("output2", [{}])
        summary = output2[0] if output2 else {}

        # 매수가능금액 조회 (TTTC8908R) — 실제 주문 가능한 정확한 금액
        try:
            cash_balance = self.get_buying_power()
        except Exception:
            logger.warning("Buying power API failed, falling back to prvs_rcdl_excc_amt")
            cash_balance = int(summary.get("prvs_rcdl_excc_amt", 0))

        stock_eval = int(summary.get("scts_evlu_amt", 0))

        return {
            "positions": positions,
            "cash_balance": cash_balance,
            "total_asset": cash_balance + stock_eval,
            "stock_eval_amount": stock_eval,
        }

    def get_buying_power(self) -> int:
        """매수가능금액 조회 (TTTC8908R). 미수 없는 순수 주문가능금액 반환."""
        tr_id = "TTTC8908R"
        if self._config.is_paper:
            tr_id = "VTTC8908R"

        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            tr_id=tr_id,
            params={
                "CANO": self._config.account_no,
                "ACNT_PRDT_CD": self._config.account_product_code,
                "PDNO": "005930",
                "ORD_UNPR": "0",
                "ORD_DVSN": "01",
                "CMA_EVLU_AMT_ICLD_YN": "Y",
                "OVRS_ICLD_YN": "N",
            },
        )
        output = data.get("output", {})
        # nrcvb_buy_amt: 미수없는매수금액 (미수 없이 매수 가능한 금액)
        nrcvb = output.get("nrcvb_buy_amt", "")
        if nrcvb and nrcvb.strip():
            return int(nrcvb)
        # 폴백: ord_psbl_cash (주문가능현금)
        ord_cash = output.get("ord_psbl_cash", "")
        if ord_cash and ord_cash.strip():
            return int(ord_cash)
        return 0

    def is_trading_day(self, target_date: date | None = None) -> bool:
        """거래일 여부 확인 (CTCA0903R)."""
        target = target_date or date.today()
        try:
            data = self._request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/chk-holiday",
                tr_id="CTCA0903R",
                params={
                    "BASS_DT": target.strftime("%Y%m%d"),
                    "CTX_AREA_NK": "",
                    "CTX_AREA_FK": "",
                },
            )
            for item in data.get("output", []):
                if item.get("bass_dt") == target.strftime("%Y%m%d"):
                    return item.get("opnd_yn", "N") == "Y"
            return True  # 기본적으로 영업일로 가정
        except Exception:
            # API 실패 시 주말만 체크
            return target.weekday() < 5

    def close(self) -> None:
        self._client.close()


# ─── Helpers ─────────────────────────────────────────────────────


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
