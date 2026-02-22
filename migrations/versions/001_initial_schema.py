"""Initial schema — 13 tables.

Revision ID: 001
Revises: None
Create Date: 2026-02-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── Master Data ─────────────────────────────────────────
    op.create_table(
        "stock_masters",
        sa.Column("stock_code", sa.String(10), primary_key=True),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("market", sa.String(10), server_default="KOSPI"),
        sa.Column("market_cap", sa.BigInteger, nullable=True),
        sa.Column("sector_naver", sa.String(50), nullable=True),
        sa.Column("sector_group", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_stock_masters_sector", "stock_masters", ["sector_group"])
    op.create_index("ix_stock_masters_active", "stock_masters", ["is_active", "market"])

    op.create_table(
        "configs",
        sa.Column("config_key", sa.String(100), primary_key=True),
        sa.Column("config_value", sa.String(10000), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ─── Market Data ─────────────────────────────────────────
    op.create_table(
        "stock_daily_prices",
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), primary_key=True),
        sa.Column("price_date", sa.Date, primary_key=True),
        sa.Column("open_price", sa.Integer, nullable=False),
        sa.Column("high_price", sa.Integer, nullable=False),
        sa.Column("low_price", sa.Integer, nullable=False),
        sa.Column("close_price", sa.Integer, nullable=False),
        sa.Column("volume", sa.Integer, nullable=False),
        sa.Column("change_pct", sa.Float, nullable=True),
    )
    op.create_index("ix_daily_prices_date", "stock_daily_prices", ["price_date"])

    op.create_table(
        "stock_investor_tradings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code")),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("foreign_net_buy", sa.Float, nullable=True),
        sa.Column("institution_net_buy", sa.Float, nullable=True),
        sa.Column("individual_net_buy", sa.Float, nullable=True),
        sa.Column("foreign_holding_ratio", sa.Float, nullable=True),
        sa.UniqueConstraint("stock_code", "trade_date", name="uq_investor_code_date"),
    )
    op.create_index("ix_investor_date", "stock_investor_tradings", ["trade_date", "stock_code"])

    op.create_table(
        "stock_fundamentals",
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), primary_key=True),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("per", sa.Float, nullable=True),
        sa.Column("pbr", sa.Float, nullable=True),
        sa.Column("roe", sa.Float, nullable=True),
        sa.Column("market_cap", sa.BigInteger, nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ─── Analysis Data ───────────────────────────────────────
    op.create_table(
        "daily_quant_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("score_date", sa.Date, nullable=False),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code")),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("total_quant_score", sa.Float, nullable=False),
        sa.Column("momentum_score", sa.Float, nullable=False),
        sa.Column("quality_score", sa.Float, nullable=False),
        sa.Column("value_score", sa.Float, nullable=False),
        sa.Column("technical_score", sa.Float, nullable=False),
        sa.Column("news_score", sa.Float, nullable=False),
        sa.Column("supply_demand_score", sa.Float, nullable=False),
        sa.Column("llm_score", sa.Float, nullable=True),
        sa.Column("hybrid_score", sa.Float, nullable=True),
        sa.Column("risk_tag", sa.String(30), nullable=True),
        sa.Column("trade_tier", sa.String(10), nullable=True),
        sa.Column("is_tradable", sa.Boolean, server_default=sa.text("1")),
        sa.Column("is_final_selected", sa.Boolean, server_default=sa.text("0")),
        sa.Column("llm_reason", sa.String(2000), nullable=True),
        sa.UniqueConstraint("score_date", "stock_code", name="uq_quant_date_code"),
    )
    op.create_index("ix_quant_final", "daily_quant_scores", ["is_final_selected", "score_date"])

    op.create_table(
        "stock_news_sentiments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code")),
        sa.Column("news_date", sa.Date, nullable=False),
        sa.Column("press", sa.String(100), nullable=True),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("summary", sa.String(2000), nullable=True),
        sa.Column("sentiment_score", sa.Float, nullable=False),
        sa.Column("sentiment_reason", sa.String(2000), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("article_url", sa.String(1000), nullable=False),
        sa.Column("published_at", sa.DateTime, nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.UniqueConstraint("article_url", name="uq_news_url"),
    )
    op.create_index("ix_news_code_date", "stock_news_sentiments", ["stock_code", "news_date"])

    op.create_table(
        "daily_macro_insights",
        sa.Column("insight_date", sa.Date, primary_key=True),
        sa.Column("sentiment", sa.String(30), nullable=False),
        sa.Column("sentiment_score", sa.Integer, nullable=False),
        sa.Column("regime_hint", sa.String(200), nullable=False),
        sa.Column("sectors_to_favor", sa.String(500), nullable=True),
        sa.Column("sectors_to_avoid", sa.String(500), nullable=True),
        sa.Column("position_size_pct", sa.Integer, server_default="100"),
        sa.Column("stop_loss_adjust_pct", sa.Integer, server_default="100"),
        sa.Column("political_risk_level", sa.String(10), server_default="low"),
        sa.Column("political_risk_summary", sa.String(2000), nullable=True),
        sa.Column("vix_value", sa.Float, nullable=True),
        sa.Column("vix_regime", sa.String(20), nullable=True),
        sa.Column("usd_krw", sa.Float, nullable=True),
        sa.Column("kospi_index", sa.Float, nullable=True),
        sa.Column("kosdaq_index", sa.Float, nullable=True),
        sa.Column("sector_signals_json", sa.Text, nullable=True),
        sa.Column("key_themes_json", sa.Text, nullable=True),
        sa.Column("risk_factors_json", sa.Text, nullable=True),
        sa.Column("raw_council_output_json", sa.Text, nullable=True),
        sa.Column("council_cost_usd", sa.Float, nullable=True),
        sa.Column("data_completeness_pct", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "global_macro_snapshots",
        sa.Column("snapshot_date", sa.Date, primary_key=True),
        sa.Column("fed_rate", sa.Float, nullable=True),
        sa.Column("treasury_2y", sa.Float, nullable=True),
        sa.Column("treasury_10y", sa.Float, nullable=True),
        sa.Column("treasury_spread", sa.Float, nullable=True),
        sa.Column("us_cpi_yoy", sa.Float, nullable=True),
        sa.Column("us_unemployment", sa.Float, nullable=True),
        sa.Column("vix", sa.Float, nullable=True),
        sa.Column("vix_regime", sa.String(20), nullable=True),
        sa.Column("dxy_index", sa.Float, nullable=True),
        sa.Column("usd_krw", sa.Float, nullable=True),
        sa.Column("bok_rate", sa.Float, nullable=True),
        sa.Column("kospi_index", sa.Float, nullable=True),
        sa.Column("kospi_change_pct", sa.Float, nullable=True),
        sa.Column("kosdaq_index", sa.Float, nullable=True),
        sa.Column("kosdaq_change_pct", sa.Float, nullable=True),
        sa.Column("kospi_foreign_net", sa.Float, nullable=True),
        sa.Column("kosdaq_foreign_net", sa.Float, nullable=True),
        sa.Column("kospi_institutional_net", sa.Float, nullable=True),
        sa.Column("kospi_retail_net", sa.Float, nullable=True),
        sa.Column("completeness_pct", sa.Float, nullable=True),
        sa.Column("data_sources_json", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    # ─── Trading Data ────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), primary_key=True),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("average_buy_price", sa.Integer, nullable=False),
        sa.Column("total_buy_amount", sa.Integer, nullable=False),
        sa.Column("sector_group", sa.String(30), nullable=True),
        sa.Column("high_watermark", sa.Integer, nullable=True),
        sa.Column("stop_loss_price", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "trade_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code")),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("trade_type", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("total_amount", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("strategy_signal", sa.String(50), nullable=True),
        sa.Column("market_regime", sa.String(20), nullable=True),
        sa.Column("llm_score", sa.Float, nullable=True),
        sa.Column("hybrid_score", sa.Float, nullable=True),
        sa.Column("trade_tier", sa.String(10), nullable=True),
        sa.Column("profit_pct", sa.Float, nullable=True),
        sa.Column("profit_amount", sa.Integer, nullable=True),
        sa.Column("holding_days", sa.Integer, nullable=True),
        sa.Column("trade_timestamp", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_trade_logs_code_time", "trade_logs", ["stock_code", "trade_timestamp"])
    op.create_index("ix_trade_logs_type_time", "trade_logs", ["trade_type", "trade_timestamp"])

    op.create_table(
        "daily_asset_snapshots",
        sa.Column("snapshot_date", sa.Date, primary_key=True),
        sa.Column("total_asset", sa.Integer, nullable=False),
        sa.Column("cash_balance", sa.Integer, nullable=False),
        sa.Column("stock_eval_amount", sa.Integer, nullable=False),
        sa.Column("total_profit_loss", sa.Integer, nullable=True),
        sa.Column("realized_profit_loss", sa.Integer, nullable=True),
        sa.Column("net_investment", sa.Integer, nullable=True),
        sa.Column("position_count", sa.Integer, server_default="0"),
    )

    op.create_table(
        "watchlist_histories",
        sa.Column("snapshot_date", sa.Date, primary_key=True),
        sa.Column("stock_code", sa.String(10), sa.ForeignKey("stock_masters.stock_code"), primary_key=True),
        sa.Column("stock_name", sa.String(100), nullable=False),
        sa.Column("llm_score", sa.Float, nullable=True),
        sa.Column("hybrid_score", sa.Float, nullable=True),
        sa.Column("is_tradable", sa.Boolean, server_default=sa.text("1")),
        sa.Column("trade_tier", sa.String(10), nullable=True),
        sa.Column("risk_tag", sa.String(30), nullable=True),
        sa.Column("rank", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("watchlist_histories")
    op.drop_table("daily_asset_snapshots")
    op.drop_table("trade_logs")
    op.drop_table("positions")
    op.drop_table("global_macro_snapshots")
    op.drop_table("daily_macro_insights")
    op.drop_table("stock_news_sentiments")
    op.drop_table("daily_quant_scores")
    op.drop_table("stock_fundamentals")
    op.drop_table("stock_investor_tradings")
    op.drop_table("stock_daily_prices")
    op.drop_table("configs")
    op.drop_table("stock_masters")
