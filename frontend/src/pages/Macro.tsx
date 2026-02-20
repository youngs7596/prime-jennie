import { useState } from "react";
import { useMacroInsight, useMacroDates } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import {
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Shield,
  Target,
  BarChart3,
  Activity,
  Users,
  Zap,
  CheckCircle,
} from "lucide-react";

const VIX_COLORS: Record<string, string> = {
  crisis: "bg-accent-red/20 border-accent-red/40",
  elevated: "bg-accent-yellow/20 border-accent-yellow/40",
  normal: "bg-accent-blue/20 border-accent-blue/40",
  low_vol: "bg-accent-green/20 border-accent-green/40",
};

const RISK_COLORS: Record<string, string> = {
  critical: "bg-accent-red/15 border-accent-red/30 text-accent-red",
  high: "bg-accent-orange/15 border-accent-orange/30 text-accent-orange",
  medium: "bg-accent-yellow/15 border-accent-yellow/30 text-accent-yellow",
  low: "bg-accent-green/15 border-accent-green/30 text-accent-green",
};

const CONSENSUS_LABELS: Record<string, string> = {
  strong_agree: "Strong Agree",
  agree: "Agree",
  partial_disagree: "Partial Disagree",
  disagree: "Disagree",
};

function formatChange(val: number | null | undefined) {
  if (val == null) return null;
  const sign = val >= 0 ? "+" : "";
  return `${sign}${val.toFixed(2)}%`;
}

function formatFlow(val: number | null | undefined) {
  if (val == null) return "-";
  const sign = val >= 0 ? "+" : "";
  return `${sign}${val.toLocaleString()}억`;
}

export default function Macro() {
  const [selectedDate, setSelectedDate] = useState<string | undefined>();
  const dates = useMacroDates(30);
  const insight = useMacroInsight(selectedDate);

  const data = insight.data;
  const noData = data && "status" in data && (data as Record<string, unknown>).status === "no_data";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Macro Council</h1>
          {data && !noData && (
            <p className="text-xs text-text-muted mt-0.5">{data.insight_date}</p>
          )}
        </div>

        {dates.data && dates.data.length > 0 && (
          <select
            value={selectedDate ?? ""}
            onChange={(e) => setSelectedDate(e.target.value || undefined)}
            className="rounded-md border border-border-primary bg-bg-secondary px-3 py-1.5 text-sm text-text-primary"
          >
            <option value="">Latest</option>
            {dates.data.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        )}
      </div>

      {insight.isLoading && <LoadingSpinner />}

      {noData && (
        <p className="py-8 text-center text-sm text-text-muted">
          No macro insight available
        </p>
      )}

      {data && !noData && (
        <>
          {/* Section 1: Summary Cards */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {/* Sentiment */}
            <Card>
              <p className="text-xs text-text-secondary mb-1">Sentiment</p>
              <p className="text-2xl font-bold mb-1">{data.sentiment_score}</p>
              <StatusBadge status={data.sentiment} size="md" />
            </Card>

            {/* VIX */}
            <Card className={`border ${VIX_COLORS[data.vix_regime ?? "normal"] ?? VIX_COLORS.normal}`}>
              <p className="text-xs text-text-secondary mb-1">VIX</p>
              <p className="text-2xl font-bold">{data.vix_value?.toFixed(1) ?? "-"}</p>
              <span className="text-xs text-text-secondary capitalize">{data.vix_regime ?? "unknown"}</span>
            </Card>

            {/* Position Size */}
            <Card className="border border-accent-blue/30 bg-accent-blue/10">
              <div className="flex items-center gap-2 mb-1">
                <Target size={14} className="text-accent-blue" />
                <p className="text-xs text-text-secondary">Position Size</p>
              </div>
              <p className="text-2xl font-bold text-accent-blue">{data.position_size_pct}%</p>
            </Card>

            {/* Stop Loss */}
            <Card className="border border-accent-orange/30 bg-accent-orange/10">
              <div className="flex items-center gap-2 mb-1">
                <Shield size={14} className="text-accent-orange" />
                <p className="text-xs text-text-secondary">Stop Loss</p>
              </div>
              <p className="text-2xl font-bold text-accent-orange">{data.stop_loss_adjust_pct}%</p>
            </Card>
          </div>

          {/* Section 2: Global Market Data */}
          <Card title="Global Market Data">
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              {/* Left: Market Indicators */}
              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg bg-bg-tertiary p-3">
                  <p className="text-xs text-text-muted mb-1">USD/KRW</p>
                  <p className="text-lg font-mono font-bold">{data.usd_krw?.toFixed(0) ?? "-"}</p>
                </div>
                <div className="rounded-lg bg-bg-tertiary p-3">
                  <p className="text-xs text-text-muted mb-1">KOSPI</p>
                  <p className="text-lg font-mono font-bold">{data.kospi_index?.toFixed(1) ?? "-"}</p>
                  {data.kospi_change_pct != null && (
                    <p className={`text-xs font-mono ${data.kospi_change_pct >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                      {formatChange(data.kospi_change_pct)}
                    </p>
                  )}
                </div>
                <div className="rounded-lg bg-bg-tertiary p-3">
                  <p className="text-xs text-text-muted mb-1">KOSDAQ</p>
                  <p className="text-lg font-mono font-bold">{data.kosdaq_index?.toFixed(1) ?? "-"}</p>
                  {data.kosdaq_change_pct != null && (
                    <p className={`text-xs font-mono ${data.kosdaq_change_pct >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                      {formatChange(data.kosdaq_change_pct)}
                    </p>
                  )}
                </div>
                <div className="rounded-lg bg-bg-tertiary p-3">
                  <p className="text-xs text-text-muted mb-1">Data Completeness</p>
                  <p className="text-lg font-mono font-bold">{data.data_completeness_pct ?? "-"}%</p>
                </div>
              </div>

              {/* Right: Investor Flow */}
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <Users size={16} className="text-accent-purple" />
                  <p className="text-sm font-medium">Investor Flow (KOSPI)</p>
                </div>
                <div className="space-y-2">
                  <FlowRow label="외국인" value={data.kospi_foreign_net} />
                  <FlowRow label="기관" value={data.kospi_institutional_net} />
                  <FlowRow label="개인" value={data.kospi_retail_net} />
                </div>
              </div>
            </div>
          </Card>

          {/* Section 3: Trading Strategy */}
          <Card title="Trading Strategy">
            <div className="space-y-4">
              {/* Regime + Reasoning */}
              <div className="flex items-start gap-3">
                <StatusBadge status={data.sentiment} size="sm" />
                <p className="text-sm text-text-secondary leading-relaxed flex-1">
                  {data.trading_reasoning ?? data.regime_hint ?? "-"}
                </p>
              </div>

              {/* Strategy & Sector Grid */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {/* Allowed Strategies */}
                <div className="rounded-lg border border-accent-green/20 bg-accent-green/5 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp size={14} className="text-accent-green" />
                    <p className="text-xs font-medium text-accent-green">Allowed Strategies</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(data.strategies_to_favor?.length ?? 0) > 0
                      ? data.strategies_to_favor.map((s) => (
                          <span key={s} className="rounded-full bg-accent-green/15 px-2.5 py-0.5 text-xs text-accent-green">
                            {s}
                          </span>
                        ))
                      : <span className="text-xs text-text-muted">-</span>}
                  </div>
                </div>

                {/* Avoided Strategies */}
                <div className="rounded-lg border border-accent-red/20 bg-accent-red/5 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingDown size={14} className="text-accent-red" />
                    <p className="text-xs font-medium text-accent-red">Avoided Strategies</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(data.strategies_to_avoid?.length ?? 0) > 0
                      ? data.strategies_to_avoid.map((s) => (
                          <span key={s} className="rounded-full bg-accent-red/15 px-2.5 py-0.5 text-xs text-accent-red">
                            {s}
                          </span>
                        ))
                      : <span className="text-xs text-text-muted">-</span>}
                  </div>
                </div>

                {/* Favored Sectors */}
                {data.sectors_to_favor && (
                  <div className="rounded-lg border border-accent-blue/20 bg-accent-blue/5 p-3">
                    <div className="flex items-center gap-2 mb-2">
                      <Zap size={14} className="text-accent-blue" />
                      <p className="text-xs font-medium text-accent-blue">Favored Sectors</p>
                    </div>
                    <ul className="space-y-1">
                      {data.sectors_to_favor.split(", ").map((s) => (
                        <li key={s} className="text-xs text-text-secondary">{s}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Avoided Sectors */}
                {data.sectors_to_avoid && (
                  <div className="rounded-lg border border-border-primary bg-bg-tertiary p-3">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertTriangle size={14} className="text-text-muted" />
                      <p className="text-xs font-medium text-text-muted">Avoided Sectors</p>
                    </div>
                    <ul className="space-y-1">
                      {data.sectors_to_avoid.split(", ").map((s) => (
                        <li key={s} className="text-xs text-text-muted">{s}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* Section 4: Risk & Opportunity */}
          <Card title="Risk & Opportunity Analysis">
            <div className="space-y-4">
              {/* Political Risk Banner */}
              <div className={`rounded-lg border p-3 ${RISK_COLORS[data.political_risk_level] ?? RISK_COLORS.low}`}>
                <div className="flex items-center gap-2 mb-1">
                  <AlertTriangle size={14} />
                  <span className="text-xs font-medium uppercase">Political Risk: {data.political_risk_level}</span>
                </div>
                {data.political_risk_summary && (
                  <p className="text-xs leading-relaxed opacity-80">{data.political_risk_summary}</p>
                )}
              </div>

              {/* Risk + Opportunity Columns */}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {/* Risk Factors */}
                <div>
                  <p className="text-xs font-medium text-accent-red mb-2 flex items-center gap-1.5">
                    <Activity size={12} /> Risk Factors
                  </p>
                  {(data.risk_factors?.length ?? 0) > 0 ? (
                    <ul className="space-y-1.5">
                      {data.risk_factors.map((rf, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs">
                          <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-red" />
                          <span className="text-text-secondary">
                            {rf.name}
                            {rf.severity && (
                              <span className="ml-1 text-text-muted">({rf.severity})</span>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-text-muted">No risk factors identified</p>
                  )}
                </div>

                {/* Opportunity Factors */}
                <div>
                  <p className="text-xs font-medium text-accent-green mb-2 flex items-center gap-1.5">
                    <CheckCircle size={12} /> Opportunity Factors
                  </p>
                  {(data.opportunity_factors?.length ?? 0) > 0 ? (
                    <ul className="space-y-1.5">
                      {data.opportunity_factors.map((of_, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs">
                          <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-green" />
                          <span className="text-text-secondary">{of_}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-text-muted">No opportunities identified</p>
                  )}
                </div>
              </div>
            </div>
          </Card>

          {/* Section 5: Sector Signals */}
          {data.sector_signals && data.sector_signals.length > 0 && (
            <Card title="Sector Signals">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.sector_signals.map((s, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 rounded-md border border-border-primary bg-bg-primary p-3"
                  >
                    {s.signal?.toUpperCase() === "BULLISH" || s.signal?.toUpperCase() === "HOT" ? (
                      <TrendingUp size={14} className="mt-0.5 shrink-0 text-accent-green" />
                    ) : s.signal?.toUpperCase() === "BEARISH" || s.signal?.toUpperCase() === "AVOID" ? (
                      <TrendingDown size={14} className="mt-0.5 shrink-0 text-accent-red" />
                    ) : (
                      <BarChart3 size={14} className="mt-0.5 shrink-0 text-accent-yellow" />
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{s.sector_group}</p>
                      <p className="text-xs text-text-muted">{s.signal}</p>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Section 6: Council Info */}
          <Card title="Council Info">
            <div className="flex flex-wrap items-center gap-4">
              {data.council_consensus && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-muted">Consensus:</span>
                  <StatusBadge status={data.council_consensus} size="sm" />
                  <span className="text-xs text-text-secondary">
                    {CONSENSUS_LABELS[data.council_consensus] ?? data.council_consensus}
                  </span>
                </div>
              )}
              {data.council_cost_usd != null && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-muted">Cost:</span>
                  <span className="text-sm font-mono">${data.council_cost_usd.toFixed(3)}</span>
                  <span className="text-xs text-text-muted">
                    (~{Math.round(data.council_cost_usd * 1450).toLocaleString()}원)
                  </span>
                </div>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function FlowRow({ label, value }: { label: string; value: number | null | undefined }) {
  const formatted = formatFlow(value);
  const color = value == null ? "" : value >= 0 ? "text-accent-green" : "text-accent-red";
  return (
    <div className="flex items-center justify-between rounded-md bg-bg-tertiary px-3 py-2">
      <span className="text-sm text-text-secondary">{label}</span>
      <span className={`text-sm font-mono font-medium ${color}`}>{formatted}</span>
    </div>
  );
}
