-- ================================================================
-- prime-jennie: Development DB VIEW Setup
-- ================================================================
-- jennie_db_dev의 참조 테이블 14개를 운영 DB(jennie_db)의 VIEW로 교체
-- 거래 테이블 3개(positions, trade_logs, daily_asset_snapshots)는 유지
--
-- 사용법:
--   mariadb -h 192.168.31.195 -P 3307 -u jennie -p jennie_db_dev < scripts/setup_dev_views.sql
-- ================================================================

USE jennie_db_dev;

SET FOREIGN_KEY_CHECKS = 0;

-- ─── Master / Reference ──────────────────────────────────────────
DROP TABLE IF EXISTS stock_masters;
CREATE VIEW stock_masters AS SELECT * FROM jennie_db.stock_masters;

DROP TABLE IF EXISTS configs;
CREATE VIEW configs AS SELECT * FROM jennie_db.configs;

-- ─── Market Data ─────────────────────────────────────────────────
DROP TABLE IF EXISTS stock_daily_prices;
CREATE VIEW stock_daily_prices AS SELECT * FROM jennie_db.stock_daily_prices;

DROP TABLE IF EXISTS index_daily_prices;
CREATE VIEW index_daily_prices AS SELECT * FROM jennie_db.index_daily_prices;

DROP TABLE IF EXISTS stock_minute_prices;
CREATE VIEW stock_minute_prices AS SELECT * FROM jennie_db.stock_minute_prices;

DROP TABLE IF EXISTS stock_investor_tradings;
CREATE VIEW stock_investor_tradings AS SELECT * FROM jennie_db.stock_investor_tradings;

DROP TABLE IF EXISTS stock_fundamentals;
CREATE VIEW stock_fundamentals AS SELECT * FROM jennie_db.stock_fundamentals;

DROP TABLE IF EXISTS stock_consensus;
CREATE VIEW stock_consensus AS SELECT * FROM jennie_db.stock_consensus;

-- ─── Corporate / News ────────────────────────────────────────────
DROP TABLE IF EXISTS stock_disclosures;
CREATE VIEW stock_disclosures AS SELECT * FROM jennie_db.stock_disclosures;

DROP TABLE IF EXISTS stock_news_sentiments;
CREATE VIEW stock_news_sentiments AS SELECT * FROM jennie_db.stock_news_sentiments;

-- ─── Quant / Macro ───────────────────────────────────────────────
DROP TABLE IF EXISTS daily_quant_scores;
CREATE VIEW daily_quant_scores AS SELECT * FROM jennie_db.daily_quant_scores;

DROP TABLE IF EXISTS daily_macro_insights;
CREATE VIEW daily_macro_insights AS SELECT * FROM jennie_db.daily_macro_insights;

DROP TABLE IF EXISTS global_macro_snapshots;
CREATE VIEW global_macro_snapshots AS SELECT * FROM jennie_db.global_macro_snapshots;

-- ─── Watchlist ───────────────────────────────────────────────────
DROP TABLE IF EXISTS watchlist_histories;
CREATE VIEW watchlist_histories AS SELECT * FROM jennie_db.watchlist_histories;

SET FOREIGN_KEY_CHECKS = 1;
