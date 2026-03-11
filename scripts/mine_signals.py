#!/usr/bin/env python3
"""데이터마이닝 기반 최적 매수 시점 탐색.

과거 일봉 + 뉴스 감성 데이터에서:
1. Forward return 기반으로 최적 매수 지점을 라벨링 (익일 시가 매수 → N일 후 종가)
2. 각 시점의 기술적 지표 + 뉴스 피처를 계산
3. Random Forest로 어떤 피처 조합이 수익을 예측하는지 탐색
4. Permutation Importance로 Impurity-based 결과를 교차검증

v2 변경 (2026-03-11, 민지 리뷰 반영):
- 라벨링: 당일 종가 → 익일 시가 매수 기준으로 수정 (실현 가능한 수익률)
- Permutation Importance 추가 (RF impurity-based 편향 검증)

Usage:
    uv run python -m scripts.mine_signals
    uv run python -m scripts.mine_signals --start 2025-09-01 --forward-days 5 --threshold 3.0
    uv run python -m scripts.mine_signals --top-stocks 200
"""

from __future__ import annotations

import argparse
import logging
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit
from sqlmodel import Session, text

from prime_jennie.infra.database.engine import get_engine

# SQLAlchemy echo 끄기
for _logger_name in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)
    logging.getLogger(_logger_name).propagate = False

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────────────

DEFAULT_START = "2025-09-01"
DEFAULT_END = "2026-03-11"
FORWARD_DAYS = 5  # N일 후 수익률로 라벨링
BUY_THRESHOLD = 3.0  # +N% 이상이면 좋은 매수 시점
SELL_THRESHOLD = -3.0  # -N% 이하면 피해야 할 시점
MIN_HISTORY = 60  # 기술적 지표 계산에 필요한 최소 일봉 수
TOP_STOCKS = 0  # 시총 상위 N종목만 (0=전체)


# ── 1. 데이터 로드 ────────────────────────────────────────────────────


def load_daily_prices(session: Session, start: date, end: date, top_n: int = 0) -> pd.DataFrame:
    """일봉 데이터 로드. 피처 계산을 위해 start보다 MIN_HISTORY일 앞부터 조회."""
    buffer_start = start - timedelta(days=MIN_HISTORY * 2)  # 주말/공휴일 감안

    if top_n > 0:
        # MariaDB는 서브쿼리 LIMIT 미지원 → 파생 테이블 JOIN
        query = text(f"""
            SELECT p.stock_code, p.price_date, p.open_price, p.high_price,
                   p.low_price, p.close_price, p.volume, p.change_pct
            FROM stock_daily_prices p
            JOIN (
                SELECT stock_code FROM stock_masters
                WHERE is_active = 1 AND market = 'KOSPI'
                ORDER BY market_cap DESC LIMIT {top_n}
            ) top ON p.stock_code = top.stock_code
            WHERE p.price_date BETWEEN :buf_start AND :end
            ORDER BY p.stock_code, p.price_date
        """)
    else:
        query = text("""
            SELECT p.stock_code, p.price_date, p.open_price, p.high_price,
                   p.low_price, p.close_price, p.volume, p.change_pct
            FROM stock_daily_prices p
            JOIN stock_masters m ON p.stock_code = m.stock_code
            WHERE p.price_date BETWEEN :buf_start AND :end
              AND m.is_active = 1
            ORDER BY p.stock_code, p.price_date
        """)
    df = pd.read_sql(query, session.connection(), params={"buf_start": buffer_start, "end": end})
    log.info("일봉 로드: %s건 (%s종목)", len(df), df["stock_code"].nunique())
    return df


def load_news_sentiments(session: Session, start: date, end: date) -> pd.DataFrame:
    """뉴스 감성 데이터를 종목·일자별로 집계."""
    buffer_start = start - timedelta(days=30)  # 감성 이동평균용 버퍼

    query = text("""
        SELECT stock_code, news_date,
               AVG(sentiment_score) AS avg_sentiment,
               COUNT(*) AS news_count,
               MAX(sentiment_score) AS max_sentiment,
               MIN(sentiment_score) AS min_sentiment
        FROM stock_news_sentiments
        WHERE news_date BETWEEN :buf_start AND :end
        GROUP BY stock_code, news_date
        ORDER BY stock_code, news_date
    """)
    df = pd.read_sql(query, session.connection(), params={"buf_start": buffer_start, "end": end})
    log.info("뉴스 감성 로드: %s건 (%s종목)", len(df), df["stock_code"].nunique())
    return df


def load_investor_trading(session: Session, start: date, end: date) -> pd.DataFrame:
    """수급 데이터 로드."""
    buffer_start = start - timedelta(days=30)

    query = text("""
        SELECT stock_code, trade_date,
               foreign_net_buy, institution_net_buy, individual_net_buy,
               foreign_holding_ratio
        FROM stock_investor_tradings
        WHERE trade_date BETWEEN :buf_start AND :end
        ORDER BY stock_code, trade_date
    """)
    df = pd.read_sql(query, session.connection(), params={"buf_start": buffer_start, "end": end})
    log.info("수급 데이터 로드: %s건 (%s종목)", len(df), df["stock_code"].nunique())
    return df


# ── 2. 기술적 지표 계산 ──────────────────────────────────────────────


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """종목별 기술적 지표를 계산하여 컬럼 추가."""
    results = []

    for stock_code, g in df.groupby("stock_code"):
        g = g.sort_values("price_date").copy()
        c = g["close_price"].astype(float)
        h = g["high_price"].astype(float)
        lo = g["low_price"].astype(float)
        o = g["open_price"].astype(float)
        v = g["volume"].astype(float)

        # RSI(14)
        g["rsi_14"] = _rsi(c, 14)

        # MACD (12, 26, 9)
        ema12 = _ema(c, 12)
        ema26 = _ema(c, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)
        g["macd_hist"] = macd_line - signal_line

        # 볼린저 밴드 %B
        sma20 = c.rolling(20).mean()
        std20 = c.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        g["bb_pct_b"] = (c - lower) / (upper - lower).replace(0, np.nan)

        # 이동평균
        sma5 = c.rolling(5).mean()
        sma60 = c.rolling(60).mean()
        g["ma5_over_ma20"] = (sma5 / sma20 - 1) * 100  # 단기 > 중기 비율
        g["price_vs_ma20"] = (c / sma20 - 1) * 100  # 이격률(20)
        g["price_vs_ma60"] = (c / sma60 - 1) * 100  # 이격률(60)

        # 이평선 정배열: SMA5 > SMA20 > SMA60
        g["ma_aligned"] = ((sma5 > sma20) & (sma20 > sma60)).astype(int)

        # 거래량 비율 (vs 20일 평균)
        vol_ma20 = v.rolling(20).mean()
        g["volume_ratio"] = v / vol_ma20.replace(0, np.nan)

        # 거래량 추세 (5일 vs 20일 평균)
        vol_ma5 = v.rolling(5).mean()
        g["volume_trend"] = vol_ma5 / vol_ma20.replace(0, np.nan)

        # ATR(14) 대비 종가 비율 (변동성)
        tr = pd.concat(
            [
                h - lo,
                (h - c.shift(1)).abs(),
                (lo - c.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr14 = tr.rolling(14).mean()
        g["atr_ratio"] = atr14 / c.replace(0, np.nan) * 100

        # 직전 N일 수익률
        g["return_5d"] = c.pct_change(5) * 100
        g["return_20d"] = c.pct_change(20) * 100

        # 연속 양봉/음봉 일수
        daily_up = (c > c.shift(1)).astype(int)
        g["consec_up"] = daily_up * (daily_up.groupby((daily_up != daily_up.shift()).cumsum()).cumcount() + 1)
        daily_dn = (c < c.shift(1)).astype(int)
        g["consec_down"] = daily_dn * (daily_dn.groupby((daily_dn != daily_dn.shift()).cumsum()).cumcount() + 1)

        # 갭 (시가 vs 전일 종가)
        g["gap_pct"] = (o / c.shift(1) - 1) * 100

        # 캔들 패턴
        body = (c - o).abs()
        wick = h - lo
        g["body_ratio"] = body / wick.replace(0, np.nan)
        g["upper_shadow"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / wick.replace(0, np.nan)
        g["lower_shadow"] = (pd.concat([c, o], axis=1).min(axis=1) - lo) / wick.replace(0, np.nan)

        # 20일 변동성 (일간 수익률의 표준편차)
        g["volatility_20d"] = c.pct_change().rolling(20).std() * 100

        # 52주(260일) 고점 대비 위치
        high_52w = h.rolling(min(260, len(g))).max()
        low_52w = lo.rolling(min(260, len(g))).min()
        g["pos_in_52w"] = (c - low_52w) / (high_52w - low_52w).replace(0, np.nan)

        results.append(g)

    return pd.concat(results, ignore_index=True)


# ── 3. 뉴스 피처 ─────────────────────────────────────────────────────


def merge_news_features(prices_df: pd.DataFrame, news_df: pd.DataFrame) -> pd.DataFrame:
    """뉴스 감성을 종목·일자 기준으로 가격 데이터에 병합."""
    if news_df.empty:
        for col in [
            "news_avg_3d",
            "news_avg_7d",
            "news_trend",
            "news_count_3d",
            "news_count_7d",
            "news_max_7d",
            "news_min_7d",
        ]:
            prices_df[col] = np.nan
        return prices_df

    # 뉴스 데이터를 종목별 시계열로 변환 후 이동평균
    news_features = []
    for stock_code, g in news_df.groupby("stock_code"):
        g = g.sort_values("news_date").copy()
        g["news_date"] = pd.to_datetime(g["news_date"])
        g = g.set_index("news_date")
        # 일별 리샘플 (뉴스 없는 날은 NaN → ffill)
        g = g.resample("D").agg(
            {
                "avg_sentiment": "mean",
                "news_count": "sum",
                "max_sentiment": "max",
                "min_sentiment": "min",
            }
        )

        feat = pd.DataFrame(index=g.index)
        feat["stock_code"] = stock_code
        feat["news_avg_3d"] = g["avg_sentiment"].rolling(3, min_periods=1).mean()
        feat["news_avg_7d"] = g["avg_sentiment"].rolling(7, min_periods=1).mean()
        feat["news_trend"] = feat["news_avg_3d"] - feat["news_avg_7d"]  # 단기-장기 차이
        feat["news_count_3d"] = g["news_count"].rolling(3, min_periods=1).sum()
        feat["news_count_7d"] = g["news_count"].rolling(7, min_periods=1).sum()
        feat["news_max_7d"] = g["max_sentiment"].rolling(7, min_periods=1).max()
        feat["news_min_7d"] = g["min_sentiment"].rolling(7, min_periods=1).min()
        feat = feat.reset_index().rename(columns={"news_date": "price_date"})
        news_features.append(feat)

    if not news_features:
        for col in [
            "news_avg_3d",
            "news_avg_7d",
            "news_trend",
            "news_count_3d",
            "news_count_7d",
            "news_max_7d",
            "news_min_7d",
        ]:
            prices_df[col] = np.nan
        return prices_df

    news_feat_df = pd.concat(news_features, ignore_index=True)
    # price_date 타입 통일
    prices_df["price_date"] = pd.to_datetime(prices_df["price_date"])
    news_feat_df["price_date"] = pd.to_datetime(news_feat_df["price_date"])

    merged = prices_df.merge(
        news_feat_df,
        on=["stock_code", "price_date"],
        how="left",
    )
    return merged


def merge_supply_features(prices_df: pd.DataFrame, supply_df: pd.DataFrame) -> pd.DataFrame:
    """수급 데이터 병합."""
    if supply_df.empty:
        for col in ["frgn_net_3d", "inst_net_3d", "frgn_ratio_chg"]:
            prices_df[col] = np.nan
        return prices_df

    supply_features = []
    for stock_code, g in supply_df.groupby("stock_code"):
        g = g.sort_values("trade_date").copy()
        feat = pd.DataFrame()
        feat["price_date"] = g["trade_date"]
        feat["stock_code"] = stock_code
        feat["frgn_net_3d"] = g["foreign_net_buy"].rolling(3, min_periods=1).sum()
        feat["inst_net_3d"] = g["institution_net_buy"].rolling(3, min_periods=1).sum()
        feat["frgn_ratio_chg"] = g["foreign_holding_ratio"].diff()
        supply_features.append(feat)

    supply_feat_df = pd.concat(supply_features, ignore_index=True)
    supply_feat_df["price_date"] = pd.to_datetime(supply_feat_df["price_date"])
    prices_df["price_date"] = pd.to_datetime(prices_df["price_date"])

    merged = prices_df.merge(
        supply_feat_df,
        on=["stock_code", "price_date"],
        how="left",
    )
    return merged


# ── 4. 라벨링 ─────────────────────────────────────────────────────────


def label_forward_returns(df: pd.DataFrame, forward_days: int, buy_thr: float, sell_thr: float) -> pd.DataFrame:
    """N일 후 수익률 기반 라벨 생성.

    실전 기준: 당일 종가 시점에 피처 관측 → **익일 시가에 매수** → N거래일 후 종가에 매도.
    (민지 리뷰: 당일 종가 매수는 실현 불가능하므로 익일 시가 기준으로 수정)
    """
    results = []
    for stock_code, g in df.groupby("stock_code"):
        g = g.sort_values("price_date").copy()
        # 익일 시가 = 다음 행의 open_price
        next_open = g["open_price"].shift(-1)
        # N거래일 후 종가 = forward_days+1 행 뒤의 close_price
        # (shift(-1)이 익일이므로, 익일 기준 N일 후 = shift(-(forward_days+1)))
        future_close = g["close_price"].shift(-(forward_days + 1))
        g["entry_price"] = next_open
        g["forward_return"] = (future_close / next_open - 1) * 100

        g["label"] = "NEUTRAL"
        g.loc[g["forward_return"] >= buy_thr, "label"] = "BUY"
        g.loc[g["forward_return"] <= sell_thr, "label"] = "AVOID"
        results.append(g)

    return pd.concat(results, ignore_index=True)


# ── 5. 모델 학습 ──────────────────────────────────────────────────────

FEATURE_COLS = [
    # 기술적 지표
    "rsi_14",
    "macd_hist",
    "bb_pct_b",
    "ma5_over_ma20",
    "price_vs_ma20",
    "price_vs_ma60",
    "ma_aligned",
    "volume_ratio",
    "volume_trend",
    "atr_ratio",
    "return_5d",
    "return_20d",
    "consec_up",
    "consec_down",
    "gap_pct",
    "body_ratio",
    "upper_shadow",
    "lower_shadow",
    "volatility_20d",
    "pos_in_52w",
    # 뉴스
    "news_avg_3d",
    "news_avg_7d",
    "news_trend",
    "news_count_3d",
    "news_count_7d",
    "news_max_7d",
    "news_min_7d",
    # 수급
    "frgn_net_3d",
    "inst_net_3d",
    "frgn_ratio_chg",
]

FEATURE_LABELS_KR = {
    "rsi_14": "RSI(14)",
    "macd_hist": "MACD 히스토그램",
    "bb_pct_b": "볼린저 %B",
    "ma5_over_ma20": "MA5/MA20 비율",
    "price_vs_ma20": "이격률(20일)",
    "price_vs_ma60": "이격률(60일)",
    "ma_aligned": "이평선 정배열",
    "volume_ratio": "거래량 비율(vs 20일)",
    "volume_trend": "거래량 추세(5d/20d)",
    "atr_ratio": "ATR 비율(%)",
    "return_5d": "직전 5일 수익률",
    "return_20d": "직전 20일 수익률",
    "consec_up": "연속 상승일",
    "consec_down": "연속 하락일",
    "gap_pct": "갭(%)",
    "body_ratio": "캔들 몸통 비율",
    "upper_shadow": "윗꼬리 비율",
    "lower_shadow": "아래꼬리 비율",
    "volatility_20d": "20일 변동성",
    "pos_in_52w": "52주 범위 내 위치",
    "news_avg_3d": "뉴스 감성(3일)",
    "news_avg_7d": "뉴스 감성(7일)",
    "news_trend": "감성 추세(3d-7d)",
    "news_count_3d": "뉴스 건수(3일)",
    "news_count_7d": "뉴스 건수(7일)",
    "news_max_7d": "최대 감성(7일)",
    "news_min_7d": "최소 감성(7일)",
    "frgn_net_3d": "외인 순매수(3일합)",
    "inst_net_3d": "기관 순매수(3일합)",
    "frgn_ratio_chg": "외인 비율 변화",
}


def prepare_dataset(df: pd.DataFrame, start: date) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """학습 가능한 데이터셋 준비 (버퍼 기간 제거 + 결측 처리)."""
    # 분석 기간만 (버퍼 기간 제외)
    df = df[df["price_date"] >= pd.Timestamp(start)].copy()

    # forward_return이 없는 행 (마지막 N일) 제거
    df = df.dropna(subset=["forward_return"]).reset_index(drop=True)

    # 피처 결측치 처리
    X = df[FEATURE_COLS].copy()
    X = X.fillna(X.median())

    y = df["label"]
    dates = df["price_date"]
    return X, y, dates


def train_and_evaluate(
    X: pd.DataFrame,
    y: pd.Series,
    dates: pd.Series,
    forced_split_date: str | None = None,
) -> tuple[RandomForestClassifier, pd.DataFrame]:
    """시계열 분할 교차검증으로 학습 + 평가."""

    # 시간 기반 분할
    unique_dates = dates.sort_values().unique()
    if forced_split_date:
        split_date = pd.Timestamp(forced_split_date)
    else:
        split_idx = int(len(unique_dates) * 0.8)
        split_date = unique_dates[split_idx]

    train_mask = dates < split_date
    test_mask = dates >= split_date

    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]

    log.info("학습셋: %s건, 테스트셋: %s건 (분할: %s)", len(X_train), len(X_test), str(split_date)[:10])

    # Random Forest
    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=50,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # 평가
    y_pred = model.predict(X_test)
    print("\n" + "=" * 70)
    print("  모델 평가 (테스트셋)")
    print("=" * 70)
    print(f"  학습 기간: ~ {str(split_date)[:10]}")
    print(f"  테스트 기간: {str(split_date)[:10]} ~")
    print(f"  학습셋: {len(X_train):,}건 / 테스트셋: {len(X_test):,}건")
    print()
    print(classification_report(y_test, y_pred, zero_division=0))
    print("혼동 행렬:")
    labels = sorted(y.unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    cm_df = pd.DataFrame(cm, index=[f"실제_{l}" for l in labels], columns=[f"예측_{l}" for l in labels])
    print(cm_df.to_string())

    # Feature Importance (Impurity-based)
    importance = pd.DataFrame(
        {
            "feature": FEATURE_COLS,
            "feature_kr": [FEATURE_LABELS_KR.get(f, f) for f in FEATURE_COLS],
            "importance": model.feature_importances_,
        }
    ).sort_values("importance", ascending=False)

    # Permutation Importance (민지 리뷰: impurity-based는 연속형 피처에 편향)
    log.info("Permutation Importance 계산 중 (테스트셋 기준)...")
    perm_result = permutation_importance(
        model,
        X_test,
        y_test,
        n_repeats=10,
        random_state=42,
        n_jobs=-1,
    )
    perm_importance = pd.DataFrame(
        {
            "feature": FEATURE_COLS,
            "feature_kr": [FEATURE_LABELS_KR.get(f, f) for f in FEATURE_COLS],
            "perm_importance_mean": perm_result.importances_mean,
            "perm_importance_std": perm_result.importances_std,
        }
    ).sort_values("perm_importance_mean", ascending=False)

    # 두 방식 병합하여 비교
    importance = importance.merge(
        perm_importance[["feature", "perm_importance_mean", "perm_importance_std"]],
        on="feature",
    )

    return model, importance


# ── 6. 패턴 분석 ──────────────────────────────────────────────────────


def analyze_buy_patterns(df: pd.DataFrame, importance: pd.DataFrame, top_n: int = 10):
    """BUY 라벨 데이터의 피처 분포를 분석하여 패턴 추출."""
    # Impurity-based vs Permutation 비교
    print("\n" + "=" * 80)
    print("  피처 중요도 비교: Impurity-based vs Permutation")
    print("=" * 80)
    print(f"  {'피처':>20s} │ {'Impurity':>9s} {'순위':>4s} │ {'Permutation':>11s} {'순위':>4s} │ {'일치':>4s}")
    print("  " + "─" * 70)

    # Impurity 기준 정렬
    imp_sorted = importance.sort_values("importance", ascending=False).reset_index(drop=True)
    perm_sorted = importance.sort_values("perm_importance_mean", ascending=False).reset_index(drop=True)

    # 순위 매핑
    imp_rank = {row["feature"]: i + 1 for i, row in imp_sorted.iterrows()}
    perm_rank = {row["feature"]: i + 1 for i, row in perm_sorted.iterrows()}

    for _, row in imp_sorted.head(15).iterrows():
        feat = row["feature"]
        ir = imp_rank[feat]
        pr = perm_rank[feat]
        diff = abs(ir - pr)
        match = "O" if diff <= 2 else f"+{diff}"
        perm_val = row["perm_importance_mean"]
        print(
            f"  {row['feature_kr']:>20s} │ {row['importance']:>8.4f} [{ir:>2d}위] │ "
            f"{perm_val:>10.4f} [{pr:>2d}위] │ {match:>4s}"
        )

    # BUY vs NEUTRAL vs AVOID 비교
    top_features = importance.head(top_n)["feature"].tolist()
    analysis_df = df[df["forward_return"].notna()].copy()

    print("\n" + "=" * 70)
    print("  상위 피처별 BUY vs AVOID 평균 비교")
    print("=" * 70)
    print(f"  {'피처':>20s} │ {'BUY 평균':>10s} │ {'NEUTRAL 평균':>12s} │ {'AVOID 평균':>12s} │ {'차이(B-A)':>10s}")
    print("  " + "─" * 75)

    for feat in top_features:
        buy_mean = analysis_df.loc[analysis_df["label"] == "BUY", feat].mean()
        neu_mean = analysis_df.loc[analysis_df["label"] == "NEUTRAL", feat].mean()
        avoid_mean = analysis_df.loc[analysis_df["label"] == "AVOID", feat].mean()
        diff = buy_mean - avoid_mean
        kr_name = FEATURE_LABELS_KR.get(feat, feat)
        print(f"  {kr_name:>20s} │ {buy_mean:>10.2f} │ {neu_mean:>12.2f} │ {avoid_mean:>12.2f} │ {diff:>+10.2f}")

    # 최적 매수 조건 추출
    print("\n" + "=" * 70)
    print("  BUY 시점의 피처 분포 (중위값 기준)")
    print("=" * 70)

    buy_data = analysis_df[analysis_df["label"] == "BUY"]
    all_data = analysis_df

    for feat in top_features:
        buy_median = buy_data[feat].median()
        buy_q25 = buy_data[feat].quantile(0.25)
        buy_q75 = buy_data[feat].quantile(0.75)
        all_median = all_data[feat].median()
        kr_name = FEATURE_LABELS_KR.get(feat, feat)
        direction = "↑" if buy_median > all_median else "↓"
        print(
            f"  {kr_name:>20s} │ 중위값: {buy_median:>8.2f} (전체: {all_median:>8.2f} {direction}) │ "
            f"IQR: [{buy_q25:.2f} ~ {buy_q75:.2f}]"
        )


def analyze_multi_window(df_base: pd.DataFrame, start: date, buy_thr: float, sell_thr: float):
    """여러 forward return 윈도우에서 피처 중요도 비교."""
    print("\n" + "=" * 70)
    print("  멀티 윈도우 분석 (Forward Return 기간별 피처 중요도 Top 5)")
    print("=" * 70)

    for fwd in [3, 5, 10, 20]:
        labeled = label_forward_returns(df_base.copy(), fwd, buy_thr, sell_thr)
        X, y, _ = prepare_dataset(labeled, start)

        # 데이터 부족 시 스킵
        if len(X) < 1000 or y.nunique() < 2:
            print(f"\n  [{fwd}일] 데이터 부족 — 스킵")
            continue

        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=10,
            min_samples_leaf=50,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X, y)

        imp = sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: -x[1])
        buy_ratio = (y == "BUY").mean() * 100
        avoid_ratio = (y == "AVOID").mean() * 100

        print(f"\n  [{fwd}일 후] BUY={buy_ratio:.1f}% / AVOID={avoid_ratio:.1f}% (샘플: {len(X):,}건)")
        for feat, score in imp[:5]:
            kr_name = FEATURE_LABELS_KR.get(feat, feat)
            print(f"    {kr_name:>20s}: {score:.4f}")


# ── 메인 ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="데이터마이닝 기반 매수 시점 탐색")
    parser.add_argument("--start", default=DEFAULT_START, help="분석 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="분석 종료일 (YYYY-MM-DD)")
    parser.add_argument("--forward-days", type=int, default=FORWARD_DAYS, help="Forward return 일수")
    parser.add_argument("--threshold", type=float, default=BUY_THRESHOLD, help="BUY/AVOID 기준 수익률(%%)")
    parser.add_argument("--top-stocks", type=int, default=TOP_STOCKS, help="시총 상위 N종목 (0=전체)")
    parser.add_argument("--multi-window", action="store_true", help="3/5/10/20일 멀티 윈도우 분석")
    parser.add_argument("--split-date", default=None, help="학습/테스트 분할 날짜 (YYYY-MM-DD, 기본: 자동 80/20)")
    parser.add_argument("--env", default=".env", help=".env 파일 경로")
    args = parser.parse_args()

    # 환경 로드
    env_path = Path(__file__).resolve().parent.parent / args.env
    load_dotenv(env_path)

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    buy_thr = args.threshold
    sell_thr = -args.threshold

    print("=" * 70)
    print("  데이터마이닝 기반 매수 시점 탐색")
    print("=" * 70)
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  Forward return: {args.forward_days}일")
    print(f"  BUY 기준: +{buy_thr}% 이상 / AVOID 기준: {sell_thr}% 이하")
    print(f"  라벨링: 익일 시가 매수 → {args.forward_days}일 후 종가 (v2)")
    if args.top_stocks:
        print(f"  대상: KOSPI 시총 상위 {args.top_stocks}종목")

    # 데이터 로드
    engine = get_engine()
    with Session(engine) as session:
        prices = load_daily_prices(session, start_date, end_date, args.top_stocks)
        news = load_news_sentiments(session, start_date, end_date)
        supply = load_investor_trading(session, start_date, end_date)

    if prices.empty:
        log.error("일봉 데이터가 없습니다.")
        return

    # 피처 엔지니어링
    log.info("기술적 지표 계산 중...")
    df = compute_technical_features(prices)
    log.info("뉴스 피처 병합 중...")
    df = merge_news_features(df, news)
    log.info("수급 피처 병합 중...")
    df = merge_supply_features(df, supply)

    # 라벨링
    log.info("Forward return 라벨링 중...")
    df = label_forward_returns(df, args.forward_days, buy_thr, sell_thr)

    # 데이터셋 준비
    X, y, dates = prepare_dataset(df, start_date)

    print(f"\n  분석 대상 데이터: {len(X):,}건")
    print(f"  라벨 분포:")
    for label, count in y.value_counts().items():
        print(f"    {label:>8s}: {count:>8,}건 ({count / len(y) * 100:.1f}%)")

    if len(X) < 1000 or y.nunique() < 2:
        log.error("학습에 충분한 데이터가 없습니다.")
        return

    # 모델 학습 + 평가
    model, importance = train_and_evaluate(X, y, dates, args.split_date)

    # 패턴 분석
    analysis_df = df[(df["price_date"] >= pd.Timestamp(start_date)) & df["forward_return"].notna()].copy()
    analyze_buy_patterns(analysis_df, importance)

    # 멀티 윈도우 (선택)
    if args.multi_window:
        analyze_multi_window(df, start_date, buy_thr, sell_thr)

    print("\n" + "=" * 70)
    print("  완료")
    print("=" * 70)


if __name__ == "__main__":
    main()
