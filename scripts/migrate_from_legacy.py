"""One-time data migration: my-prime-jennie (legacy) → prime-jennie.

Both projects share the same MariaDB (jennie_db).
Legacy tables use singular names (stock_master, tradelog, ...),
new tables use plural names (stock_masters, trade_logs, ...).
lower_case_table_names=1 이므로 테이블명 대소문자 무관.

Prerequisites:
    alembic upgrade head   # 타겟 테이블 먼저 생성

Usage:
    python scripts/migrate_from_legacy.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from prime_jennie.infra.database.engine import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────


def _count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()


def _source_exists(conn, table: str) -> bool:
    return (
        conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND LOWER(table_name) = LOWER(:t)"
            ),
            {"t": table},
        ).scalar()
        > 0
    )


def _skip_or_ready(conn, source: str, target: str) -> bool:
    """Return True if migration should proceed, False to skip."""
    if not _source_exists(conn, source):
        logger.warning("%s: source '%s' not found, skipping", target, source)
        return False
    existing = _count(conn, target)
    if existing > 0:
        logger.info("%s: already has %d rows, skipping", target, existing)
        return False
    return True


TARGET_TABLES = [
    "stock_masters", "stock_daily_prices", "stock_investor_tradings",
    "stock_news_sentiments", "daily_quant_scores", "positions",
    "trade_logs", "daily_asset_snapshots", "watchlist_histories",
    "configs", "stock_fundamentals", "daily_macro_insights", "global_macro_snapshots",
]


SOURCE_TABLES = [
    "stock_master", "stock_daily_prices_3y", "stock_investor_trading",
    "stock_news_sentiment", "daily_quant_score", "active_portfolio",
    "tradelog", "daily_asset_snapshot", "watchlist_history",
]

COLLATION = "utf8mb4_general_ci"


def unify_collation(conn) -> None:
    """소스 + 타겟 모든 테이블 collation 통일."""
    count = 0
    for t in TARGET_TABLES + SOURCE_TABLES:
        if _source_exists(conn, t):
            conn.execute(text(
                f"ALTER TABLE `{t}` CONVERT TO CHARACTER SET utf8mb4 COLLATE {COLLATION}"
            ))
            count += 1
    logger.info("Collation unified to %s for %d tables", COLLATION, count)


# ── 1. stock_master → stock_masters ──────────────────────────────


def migrate_stock_masters(conn) -> int:
    if not _skip_or_ready(conn, "stock_master", "stock_masters"):
        return 0

    result = conn.execute(text("""
        INSERT INTO stock_masters
            (stock_code, stock_name, market, market_cap,
             sector_naver, sector_group, is_active, updated_at)
        SELECT
            stock_code,
            stock_name,
            'KOSPI',
            CAST(market_cap AS SIGNED),
            sector_naver,
            sector_naver_group,
            1,
            COALESCE(created_at, NOW())
        FROM stock_master
    """))
    logger.info("stock_masters: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 2. stock_daily_prices_3y → stock_daily_prices ────────────────


def migrate_stock_daily_prices(conn) -> int:
    if not _skip_or_ready(conn, "stock_daily_prices_3y", "stock_daily_prices"):
        return 0

    result = conn.execute(text("""
        INSERT INTO stock_daily_prices
            (stock_code, price_date, open_price, high_price,
             low_price, close_price, volume, change_pct)
        SELECT
            s.stock_code,
            DATE(s.price_date),
            CAST(s.open_price AS SIGNED),
            CAST(s.high_price AS SIGNED),
            CAST(s.low_price AS SIGNED),
            CAST(s.close_price AS SIGNED),
            CAST(s.volume AS SIGNED),
            NULL
        FROM stock_daily_prices_3y s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
    """))
    logger.info("stock_daily_prices: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 3. stock_investor_trading → stock_investor_tradings ──────────


def migrate_stock_investor_tradings(conn) -> int:
    if not _skip_or_ready(conn, "stock_investor_trading", "stock_investor_tradings"):
        return 0

    result = conn.execute(text("""
        INSERT INTO stock_investor_tradings
            (stock_code, trade_date, foreign_net_buy,
             institution_net_buy, individual_net_buy, foreign_holding_ratio)
        SELECT
            s.stock_code,
            s.trade_date,
            CAST(s.foreign_net_buy AS DOUBLE),
            CAST(s.institution_net_buy AS DOUBLE),
            CAST(s.individual_net_buy AS DOUBLE),
            s.foreign_holding_ratio
        FROM stock_investor_trading s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE
            foreign_holding_ratio = VALUES(foreign_holding_ratio)
    """))
    logger.info("stock_investor_tradings: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 4. stock_news_sentiment → stock_news_sentiments ──────────────


def migrate_stock_news_sentiments(conn) -> int:
    if not _skip_or_ready(conn, "stock_news_sentiment", "stock_news_sentiments"):
        return 0

    # Source columns: HEADLINE, SUMMARY (Column('HEADLINE', ...) 매핑)
    result = conn.execute(text("""
        INSERT INTO stock_news_sentiments
            (stock_code, news_date, press, headline, summary,
             sentiment_score, sentiment_reason, category,
             article_url, published_at, source)
        SELECT
            s.stock_code,
            s.news_date,
            s.press,
            LEFT(s.HEADLINE, 500),
            LEFT(s.SUMMARY, 2000),
            s.sentiment_score,
            LEFT(s.sentiment_reason, 2000),
            s.category,
            LEFT(s.article_url, 1000),
            s.published_at,
            s.source
        FROM stock_news_sentiment s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE
            sentiment_score = VALUES(sentiment_score)
    """))
    logger.info("stock_news_sentiments: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 5. daily_quant_score → daily_quant_scores ────────────────────


def migrate_daily_quant_scores(conn) -> int:
    if not _skip_or_ready(conn, "daily_quant_score", "daily_quant_scores"):
        return 0

    # news_stat_score → news_score, is_passed_filter → is_tradable
    result = conn.execute(text("""
        INSERT INTO daily_quant_scores
            (score_date, stock_code, stock_name,
             total_quant_score, momentum_score, quality_score, value_score,
             technical_score, news_score, supply_demand_score,
             llm_score, hybrid_score, is_tradable, is_final_selected)
        SELECT
            s.score_date,
            s.stock_code,
            s.stock_name,
            s.total_quant_score,
            s.momentum_score,
            s.quality_score,
            s.value_score,
            s.technical_score,
            s.news_stat_score,
            s.supply_demand_score,
            s.llm_score,
            s.hybrid_score,
            COALESCE(s.is_passed_filter, 0),
            COALESCE(s.is_final_selected, 0)
        FROM daily_quant_score s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE
            total_quant_score = VALUES(total_quant_score)
    """))
    logger.info("daily_quant_scores: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 6. active_portfolio → positions ──────────────────────────────


def migrate_positions(conn) -> int:
    if not _skip_or_ready(conn, "active_portfolio", "positions"):
        return 0

    # current_high_price → high_watermark, Float → Int
    result = conn.execute(text("""
        INSERT INTO positions
            (stock_code, stock_name, quantity, average_buy_price,
             total_buy_amount, high_watermark, stop_loss_price,
             created_at, updated_at)
        SELECT
            s.stock_code,
            s.stock_name,
            s.quantity,
            CAST(s.average_buy_price AS SIGNED),
            CAST(s.total_buy_amount AS SIGNED),
            CAST(s.current_high_price AS SIGNED),
            CAST(s.stop_loss_price AS SIGNED),
            COALESCE(s.created_at, NOW()),
            COALESCE(s.updated_at, NOW())
        FROM active_portfolio s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE
            high_watermark = VALUES(high_watermark),
            updated_at = VALUES(updated_at)
    """))
    logger.info("positions: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 7. tradelog → trade_logs ─────────────────────────────────────


def migrate_trade_logs(conn) -> int:
    if not _skip_or_ready(conn, "tradelog", "trade_logs"):
        return 0

    # stock_name JOIN, Float→Int, total_amount = quantity * price
    result = conn.execute(text("""
        INSERT INTO trade_logs
            (stock_code, stock_name, trade_type, quantity, price,
             total_amount, reason, strategy_signal, trade_timestamp)
        SELECT
            t.stock_code,
            COALESCE(sm.stock_name, t.stock_code),
            t.trade_type,
            t.quantity,
            CAST(t.price AS SIGNED),
            CAST(t.quantity * t.price AS SIGNED),
            LEFT(COALESCE(t.reason, ''), 500),
            LEFT(t.strategy_signal, 50),
            t.trade_timestamp
        FROM tradelog t
        LEFT JOIN stock_master sm ON t.stock_code = sm.stock_code
        WHERE t.stock_code IN (SELECT stock_code FROM stock_masters)
        ORDER BY t.trade_timestamp
    """))
    logger.info("trade_logs: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 8. daily_asset_snapshot → daily_asset_snapshots ──────────────


def migrate_daily_asset_snapshots(conn) -> int:
    if not _skip_or_ready(conn, "daily_asset_snapshot", "daily_asset_snapshots"):
        return 0

    # Numeric → Int, position_count = 0
    result = conn.execute(text("""
        INSERT INTO daily_asset_snapshots
            (snapshot_date, total_asset, cash_balance, stock_eval_amount,
             total_profit_loss, realized_profit_loss, net_investment,
             position_count)
        SELECT
            s.snapshot_date,
            CAST(s.total_asset_amount AS SIGNED),
            CAST(s.cash_balance AS SIGNED),
            CAST(s.stock_eval_amount AS SIGNED),
            CAST(s.total_profit_loss AS SIGNED),
            CAST(s.realized_profit_loss AS SIGNED),
            CAST(s.net_investment AS SIGNED),
            0
        FROM daily_asset_snapshot s
        ON DUPLICATE KEY UPDATE total_asset = VALUES(total_asset)
    """))
    logger.info("daily_asset_snapshots: migrated %d rows", result.rowcount)
    return result.rowcount


# ── 9. watchlist_history → watchlist_histories ───────────────────


def migrate_watchlist_histories(conn) -> int:
    if not _skip_or_ready(conn, "watchlist_history", "watchlist_histories"):
        return 0

    # hybrid_score / trade_tier / risk_tag / rank = NULL (target defaults)
    result = conn.execute(text("""
        INSERT INTO watchlist_histories
            (snapshot_date, stock_code, stock_name, llm_score, is_tradable)
        SELECT
            s.snapshot_date,
            s.stock_code,
            s.stock_name,
            s.llm_score,
            COALESCE(s.is_tradable, 1)
        FROM watchlist_history s
        WHERE s.stock_code IN (SELECT stock_code FROM stock_masters)
        ON DUPLICATE KEY UPDATE llm_score = VALUES(llm_score)
    """))
    logger.info("watchlist_histories: migrated %d rows", result.rowcount)
    return result.rowcount


# ── main ─────────────────────────────────────────────────────────

MIGRATIONS = [
    ("1/9", "stock_masters", migrate_stock_masters),
    ("2/9", "stock_daily_prices", migrate_stock_daily_prices),
    ("3/9", "stock_investor_tradings", migrate_stock_investor_tradings),
    ("4/9", "stock_news_sentiments", migrate_stock_news_sentiments),
    ("5/9", "daily_quant_scores", migrate_daily_quant_scores),
    ("6/9", "positions", migrate_positions),
    ("7/9", "trade_logs", migrate_trade_logs),
    ("8/9", "daily_asset_snapshots", migrate_daily_asset_snapshots),
    ("9/9", "watchlist_histories", migrate_watchlist_histories),
]


def main():
    engine = get_engine()
    logger.info("=== Legacy → Prime-Jennie data migration start ===")

    # collation 통일 (레거시: utf8mb4_general_ci, 신규: utf8mb4_uca1400_ai_ci)
    with engine.begin() as conn:
        unify_collation(conn)

    total = 0
    for step, name, fn in MIGRATIONS:
        logger.info("[%s] %s ...", step, name)
        with engine.begin() as conn:
            try:
                count = fn(conn)
                total += count
            except Exception:
                logger.exception("FAILED: %s", name)
                raise

    logger.info("=== Migration complete: %d total rows migrated ===", total)


if __name__ == "__main__":
    main()
