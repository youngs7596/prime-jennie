"""미국 주요 지표 vs KOSPI 상관관계 분석 — 1회성 스크립트.

Phase 1: Yahoo Finance에서 미국 데이터 수집 + DB에서 KOSPI/삼성전자/하이닉스 로드
Phase 2: 상관관계 계산 (시초가 갭, 시차, 거래량 전조)
Phase 3: 결과 CSV + 터미널 리포트

Usage:
    uv run python scripts/analyze_us_kospi_correlation.py
    uv run python scripts/analyze_us_kospi_correlation.py --days 365
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sqlmodel import Session, select

from prime_jennie.infra.crawlers.us_market import fetch_us_market_batch
from prime_jennie.infra.database.engine import get_engine
from prime_jennie.infra.database.models import (
    IndexDailyPriceDB,
    StockDailyPriceDB,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 분석 대상
SAMSUNG = "005930"
HYNIX = "000660"
OUTPUT_DIR = Path("reports")


# ─── 데이터 로딩 ─────────────────────────────────────────────────


def load_us_data(days: int) -> pd.DataFrame:
    """Yahoo Finance에서 미국 지표 일봉 로드 → wide DataFrame."""
    logger.info("미국 시장 데이터 수집 중 (days=%d)...", days)
    batch = fetch_us_market_batch(days=days)

    frames = []
    for ticker, rows in batch.items():
        df = pd.DataFrame(
            [
                {
                    "date": r.price_date,
                    f"{ticker}_close": r.close_price,
                    f"{ticker}_change_pct": r.change_pct,
                    f"{ticker}_volume": r.volume,
                }
                for r in rows
            ]
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        frames.append(df)

    if not frames:
        logger.error("미국 데이터 수집 실패")
        sys.exit(1)

    merged = frames[0]
    for f in frames[1:]:
        merged = merged.join(f, how="outer")

    logger.info("미국 데이터: %d rows, 컬럼: %s", len(merged), list(merged.columns))
    return merged.sort_index()


def _fetch_kr_yahoo(yahoo_ticker: str, label: str, days: int) -> pd.DataFrame:
    """Yahoo Finance에서 한국 종목/지수 일봉 로드."""
    from prime_jennie.infra.crawlers.us_market import fetch_us_daily

    rows = fetch_us_daily(yahoo_ticker, days=days)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                "date": r.price_date,
                f"{label}_open": r.open_price,
                f"{label}_close": r.close_price,
                f"{label}_volume": r.volume,
            }
            for r in rows
        ]
    )
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def load_kr_data(days: int) -> pd.DataFrame:
    """한국 시장 데이터 로드 — DB 우선, 실패 시 Yahoo Finance fallback."""
    # DB 연결 시도
    try:
        engine = get_engine()
        with Session(engine) as session:
            session.exec(select(IndexDailyPriceDB).limit(1)).first()
        return _load_kr_from_db(days)
    except Exception as e:
        logger.warning("DB 연결 실패 (%s), Yahoo Finance fallback 사용", e.__class__.__name__)
        return _load_kr_from_yahoo(days)


def _load_kr_from_yahoo(days: int) -> pd.DataFrame:
    """Yahoo Finance에서 KOSPI/삼성전자/하이닉스 일봉 로드."""
    logger.info("한국 시장 데이터 수집 중 (Yahoo Finance)...")

    # Yahoo Finance 한국 티커
    kospi_df = _fetch_kr_yahoo("^KS11", "KOSPI", days)
    sec_df = _fetch_kr_yahoo("005930.KS", "SEC", days)
    hynix_df = _fetch_kr_yahoo("000660.KS", "HYNIX", days)

    merged = kospi_df.join(sec_df, how="outer").join(hynix_df, how="outer")

    # change_pct, 시초가 갭 계산
    for prefix in ["KOSPI", "SEC", "HYNIX"]:
        merged[f"{prefix}_prev_close"] = merged[f"{prefix}_close"].shift(1)
        merged[f"{prefix}_change_pct"] = (
            (merged[f"{prefix}_close"] - merged[f"{prefix}_prev_close"]) / merged[f"{prefix}_prev_close"] * 100
        )
        merged[f"{prefix}_gap_pct"] = (
            (merged[f"{prefix}_open"] - merged[f"{prefix}_prev_close"]) / merged[f"{prefix}_prev_close"] * 100
        )

    logger.info(
        "한국 데이터 (Yahoo): KOSPI %d, 삼성 %d, 하이닉스 %d rows",
        len(kospi_df),
        len(sec_df),
        len(hynix_df),
    )
    return merged.sort_index()


def _load_kr_from_db(days: int) -> pd.DataFrame:
    """DB에서 KOSPI 지수 + 삼성전자/하이닉스 일봉 로드."""
    logger.info("한국 시장 데이터 로드 중 (DB)...")
    engine = get_engine()
    cutoff = date.today() - timedelta(days=days + 30)

    with Session(engine) as session:
        kospi_rows = session.exec(
            select(IndexDailyPriceDB)
            .where(IndexDailyPriceDB.index_code == "KOSPI")
            .where(IndexDailyPriceDB.price_date >= cutoff)
            .order_by(IndexDailyPriceDB.price_date)
        ).all()

        sec_rows = session.exec(
            select(StockDailyPriceDB)
            .where(StockDailyPriceDB.stock_code == SAMSUNG)
            .where(StockDailyPriceDB.price_date >= cutoff)
            .order_by(StockDailyPriceDB.price_date)
        ).all()

        hynix_rows = session.exec(
            select(StockDailyPriceDB)
            .where(StockDailyPriceDB.stock_code == HYNIX)
            .where(StockDailyPriceDB.price_date >= cutoff)
            .order_by(StockDailyPriceDB.price_date)
        ).all()

    kospi_df = pd.DataFrame(
        [
            {
                "date": r.price_date,
                "KOSPI_open": r.open_price,
                "KOSPI_close": r.close_price,
                "KOSPI_change_pct": r.change_pct,
                "KOSPI_volume": r.volume,
            }
            for r in kospi_rows
        ]
    )

    sec_df = pd.DataFrame(
        [
            {
                "date": r.price_date,
                "SEC_open": r.open_price,
                "SEC_close": r.close_price,
                "SEC_volume": r.volume,
            }
            for r in sec_rows
        ]
    )

    hynix_df = pd.DataFrame(
        [
            {
                "date": r.price_date,
                "HYNIX_open": r.open_price,
                "HYNIX_close": r.close_price,
                "HYNIX_volume": r.volume,
            }
            for r in hynix_rows
        ]
    )

    for df in [kospi_df, sec_df, hynix_df]:
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)

    merged = kospi_df.join(sec_df, how="outer").join(hynix_df, how="outer")

    for prefix in ["KOSPI", "SEC", "HYNIX"]:
        merged[f"{prefix}_prev_close"] = merged[f"{prefix}_close"].shift(1)
        merged[f"{prefix}_gap_pct"] = (
            (merged[f"{prefix}_open"] - merged[f"{prefix}_prev_close"]) / merged[f"{prefix}_prev_close"] * 100
        )

    logger.info(
        "한국 데이터 (DB): KOSPI %d, 삼성 %d, 하이닉스 %d rows",
        len(kospi_rows),
        len(sec_rows),
        len(hynix_rows),
    )
    return merged.sort_index()


# ─── 분석 함수 ───��───────────────────────────────────────────────


def analyze_gap_correlation(us: pd.DataFrame, kr: pd.DataFrame) -> pd.DataFrame:
    """미국 전일 변동률 → 한국 시초가 갭 상관관계.

    미국 D일 종가 변동률 → 한국 D+1일 시초가 갭.
    """
    # 미국 데이터를 1일 앞으로 shift (D일 미국 → D+1일 한국에 매칭)
    us_shifted = us.shift(1)  # D일 미국 데이터가 D+1일 인덱스에 위치
    us_shifted.index = us_shifted.index + pd.Timedelta(days=1)

    # 날짜 매칭 (한국 거래일 기준)
    combined = kr.join(us_shifted, how="inner")

    us_vars = ["SOX_change_pct", "NVDA_change_pct", "SP500_change_pct", "NASDAQ_change_pct"]
    kr_vars = ["KOSPI_gap_pct", "SEC_gap_pct", "HYNIX_gap_pct"]

    results = []
    for us_v in us_vars:
        for kr_v in kr_vars:
            mask = combined[[us_v, kr_v]].dropna()
            if len(mask) < 10:
                continue
            r, p = stats.pearsonr(mask[us_v], mask[kr_v])
            # 선형 회귀 기울기
            slope, intercept, _, _, _ = stats.linregress(mask[us_v], mask[kr_v])
            results.append(
                {
                    "US 변수": us_v.replace("_change_pct", ""),
                    "KR 변수": kr_v.replace("_gap_pct", " 갭"),
                    "상관계수 (r)": round(r, 4),
                    "p-value": round(p, 6),
                    "기울기": round(slope, 4),
                    "절편": round(intercept, 4),
                    "샘플 수": len(mask),
                    "해석": f"US {us_v.split('_')[0]} 1%↓ → KR {kr_v.split('_')[0]} {abs(slope):.2f}%↓ 갭",
                }
            )

    return pd.DataFrame(results)


def analyze_cross_correlation(us: pd.DataFrame, kr: pd.DataFrame) -> pd.DataFrame:
    """교차 상관 분석 — 시차별 상관계수 (0~5일 lag).

    미국 변동이 한국에 반영되는 최적 시차 탐색.
    """
    results = []
    us_vars = ["SOX_change_pct", "NVDA_change_pct"]
    kr_vars = ["KOSPI_change_pct", "SEC_gap_pct", "HYNIX_gap_pct"]

    for us_v in us_vars:
        for kr_v in kr_vars:
            best_r = 0
            best_lag = 0
            lag_details = []

            for lag in range(0, 6):
                us_shifted = us[us_v].shift(lag)
                combined = pd.DataFrame({"us": us_shifted, "kr": kr[kr_v]}).dropna()
                if len(combined) < 10:
                    continue
                r, p = stats.pearsonr(combined["us"], combined["kr"])
                lag_details.append(f"lag{lag}={r:.3f}")
                if abs(r) > abs(best_r):
                    best_r = r
                    best_lag = lag

            results.append(
                {
                    "US 변수": us_v.replace("_change_pct", ""),
                    "KR 변수": kr_v.replace("_change_pct", "").replace("_gap_pct", " 갭"),
                    "최적 시차 (일)": best_lag,
                    "최적 상관계수": round(best_r, 4),
                    "시차별 r": " | ".join(lag_details),
                }
            )

    return pd.DataFrame(results)


def analyze_vix_impact(us: pd.DataFrame, kr: pd.DataFrame) -> pd.DataFrame:
    """VIX 수준별 KOSPI 다음날 등락폭 분포."""
    # VIX close는 SP500과 같은 날짜에 있다고 가정
    # VIX가 없으므로 별도 수집
    logger.info("VIX 데이터 수집 중...")
    from prime_jennie.infra.crawlers.us_market import fetch_us_daily

    vix_rows = fetch_us_daily("^VIX", days=500)
    if not vix_rows:
        logger.warning("VIX 데이터 수집 실패, 스킵")
        return pd.DataFrame()

    vix_df = pd.DataFrame([{"date": r.price_date, "VIX": r.close_price} for r in vix_rows])
    vix_df["date"] = pd.to_datetime(vix_df["date"])
    vix_df = vix_df.set_index("date")

    # VIX D일 → KOSPI D+1일 변동률
    vix_shifted = vix_df.shift(1)
    vix_shifted.index = vix_shifted.index + pd.Timedelta(days=1)

    combined = kr[["KOSPI_change_pct"]].join(vix_shifted, how="inner").dropna()
    if combined.empty:
        return pd.DataFrame()

    # VIX 구간별 분류
    bins = [0, 15, 20, 25, 30, 35, 100]
    labels = ["<15", "15-20", "20-25", "25-30", "30-35", "35+"]
    combined["VIX_level"] = pd.cut(combined["VIX"], bins=bins, labels=labels)

    results = []
    for level in labels:
        subset = combined[combined["VIX_level"] == level]["KOSPI_change_pct"]
        if len(subset) < 3:
            continue
        results.append(
            {
                "VIX 구간": level,
                "평균 KOSPI 변동%": round(subset.mean(), 3),
                "표준편차": round(subset.std(), 3),
                "최소": round(subset.min(), 2),
                "최대": round(subset.max(), 2),
                "샘플 수": len(subset),
                "하락 확률%": round((subset < 0).mean() * 100, 1),
            }
        )

    return pd.DataFrame(results)


def analyze_volume_precursor(us: pd.DataFrame, kr: pd.DataFrame) -> pd.DataFrame:
    """KOSPI 급락일(-2% 이상) 전일의 미국 지표 패턴 분석."""
    # KOSPI 급락일 찾기
    crash_dates = kr[kr["KOSPI_change_pct"] <= -2.0].index

    if len(crash_dates) < 3:
        logger.warning("급락일 %d건 — 분석에 불충분", len(crash_dates))
        return pd.DataFrame()

    results = []
    us_vars = ["SOX_change_pct", "NVDA_change_pct", "SP500_change_pct"]

    for us_v in us_vars:
        # 급락 전일의 미국 변동률
        pre_crash = []
        normal = []

        for crash_date in crash_dates:
            # 전일 미국 데이터 찾기
            prev_dates = us.index[us.index < crash_date]
            if len(prev_dates) == 0:
                continue
            prev_date = prev_dates[-1]
            val = us.loc[prev_date, us_v]
            if pd.notna(val):
                pre_crash.append(val)

        # 비급락일의 전일 미국 데이터
        normal_dates = kr[kr["KOSPI_change_pct"] > -2.0].index
        for normal_date in normal_dates:
            prev_dates = us.index[us.index < normal_date]
            if len(prev_dates) == 0:
                continue
            prev_date = prev_dates[-1]
            val = us.loc[prev_date, us_v]
            if pd.notna(val):
                normal.append(val)

        if len(pre_crash) < 3:
            continue

        pre_arr = np.array(pre_crash)
        normal_arr = np.array(normal)

        # t-검정: 급락 전일 vs 비급락 전일의 미국 변동률 차이
        t_stat, t_p = stats.ttest_ind(pre_arr, normal_arr, equal_var=False)

        results.append(
            {
                "US 변수": us_v.replace("_change_pct", ""),
                "급락 전일 평균%": round(pre_arr.mean(), 3),
                "비급락 전일 평균%": round(normal_arr.mean(), 3),
                "차이": round(pre_arr.mean() - normal_arr.mean(), 3),
                "t-통계량": round(t_stat, 3),
                "p-value": round(t_p, 4),
                "유의미": "✓" if t_p < 0.05 else "✗",
                "급락일 수": len(pre_crash),
            }
        )

    return pd.DataFrame(results)


# ─── 리포트 출력 ─────────────────────────────────────────────────


def print_report(title: str, df: pd.DataFrame) -> None:
    """터미널에 표 형식으로 출력."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")
    if df.empty:
        print("  (데이터 부족으로 분석 불가)")
        return
    print(df.to_string(index=False))
    print()


def main():
    parser = argparse.ArgumentParser(description="미국 vs KOSPI 상관관계 분석")
    parser.add_argument("--days", type=int, default=500, help="분석 기간 (일)")
    args = parser.parse_args()

    print(f"\n{'#' * 80}")
    print("  미국 주요 지표 vs KOSPI 상관관계 분석")
    print(f"  기간: 최근 {args.days}일 | 실행일: {date.today()}")
    print(f"{'#' * 80}")

    # 데이터 수집
    us_data = load_us_data(args.days)
    kr_data = load_kr_data(args.days)

    # ① 시초가 갭 상관관계
    gap_df = analyze_gap_correlation(us_data, kr_data)
    print_report("① 시초가 갭 상관관계 (미국 D일 종가변동 → 한국 D+1일 시초가 갭)", gap_df)

    # ② 교차 상관 (시차 분석)
    xcorr_df = analyze_cross_correlation(us_data, kr_data)
    print_report("② 교차 상관 분석 (최적 시차 탐색, 0~5일 lag)", xcorr_df)

    # ③ VIX 수준별 KOSPI 영향
    vix_df = analyze_vix_impact(us_data, kr_data)
    print_report("③ VIX 수준별 KOSPI 다음날 변동 분포", vix_df)

    # ④ 급락 전조 분석
    precursor_df = analyze_volume_precursor(us_data, kr_data)
    print_report("④ KOSPI 급락(-2%↓) 전일 미국 지표 비교 (t-검정)", precursor_df)

    # CSV 저장
    OUTPUT_DIR.mkdir(exist_ok=True)
    today_str = date.today().isoformat()

    if not gap_df.empty:
        gap_df.to_csv(OUTPUT_DIR / f"us_kr_gap_correlation_{today_str}.csv", index=False)
    if not xcorr_df.empty:
        xcorr_df.to_csv(OUTPUT_DIR / f"us_kr_cross_correlation_{today_str}.csv", index=False)
    if not vix_df.empty:
        vix_df.to_csv(OUTPUT_DIR / f"vix_kospi_impact_{today_str}.csv", index=False)
    if not precursor_df.empty:
        precursor_df.to_csv(OUTPUT_DIR / f"crash_precursor_{today_str}.csv", index=False)

    print(f"\n📊 CSV 리포트 저장 완료: {OUTPUT_DIR}/")
    print("  - us_kr_gap_correlation_*.csv")
    print("  - us_kr_cross_correlation_*.csv")
    print("  - vix_kospi_impact_*.csv")
    print("  - crash_precursor_*.csv")


if __name__ == "__main__":
    main()
