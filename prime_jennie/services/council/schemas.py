"""Macro Council JSON Schemas — LLM 구조화 출력 스키마."""

# --- Step 1: Strategist ---
MACRO_STRATEGIST_SCHEMA = {
    "type": "object",
    "required": [
        "overall_sentiment",
        "sentiment_score",
        "regime_hint",
        "sector_signals",
        "risk_factors",
        "opportunity_factors",
    ],
    "properties": {
        "overall_sentiment": {
            "type": "string",
            "enum": [
                "bullish",
                "neutral_to_bullish",
                "neutral",
                "neutral_to_bearish",
                "bearish",
            ],
        },
        "sentiment_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "regime_hint": {"type": "string", "maxLength": 50},
        "sector_signals": {
            "type": "object",
            "additionalProperties": {
                "type": "string",
                "enum": ["bullish", "neutral", "bearish"],
            },
        },
        "risk_factors": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
        },
        "opportunity_factors": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "investor_flow_analysis": {"type": "string"},
    },
}

# --- Step 2: Risk Analyst ---
MACRO_RISK_ANALYST_SCHEMA = {
    "type": "object",
    "required": [
        "risk_assessment",
        "political_risk_level",
        "position_size_pct",
        "stop_loss_adjust_pct",
    ],
    "properties": {
        "risk_assessment": {
            "type": "object",
            "properties": {
                "agree_with_sentiment": {"type": "boolean"},
                "adjusted_sentiment_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "adjustment_reason": {"type": "string"},
            },
            "required": ["agree_with_sentiment", "adjusted_sentiment_score"],
        },
        "political_risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "political_risk_summary": {"type": "string"},
        "additional_risk_factors": {
            "type": "array",
            "items": {"type": "string"},
        },
        "position_size_pct": {"type": "integer", "minimum": 50, "maximum": 130},
        "stop_loss_adjust_pct": {"type": "integer", "minimum": 80, "maximum": 150},
        "risk_reasoning": {"type": "string"},
    },
}

# --- Step 3: Chief Judge ---
MACRO_CHIEF_JUDGE_SCHEMA = {
    "type": "object",
    "required": [
        "final_sentiment",
        "final_sentiment_score",
        "final_regime_hint",
        "final_position_size_pct",
        "final_stop_loss_adjust_pct",
        "council_consensus",
    ],
    "properties": {
        "final_sentiment": {
            "type": "string",
            "enum": [
                "bullish",
                "neutral_to_bullish",
                "neutral",
                "neutral_to_bearish",
                "bearish",
            ],
        },
        "final_sentiment_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "final_regime_hint": {"type": "string", "maxLength": 50},
        "strategies_to_favor": {
            "type": "array",
            "items": {"type": "string"},
        },
        "strategies_to_avoid": {
            "type": "array",
            "items": {"type": "string"},
        },
        "sectors_to_favor": {
            "type": "array",
            "items": {"type": "string"},
        },
        "sectors_to_avoid": {
            "type": "array",
            "items": {"type": "string"},
        },
        "final_position_size_pct": {
            "type": "integer",
            "minimum": 50,
            "maximum": 130,
        },
        "final_stop_loss_adjust_pct": {
            "type": "integer",
            "minimum": 80,
            "maximum": 150,
        },
        "trading_reasoning": {"type": "string"},
        "council_consensus": {
            "type": "string",
            "enum": [
                "strong_agree",
                "agree",
                "partial_disagree",
                "disagree",
            ],
        },
    },
}
