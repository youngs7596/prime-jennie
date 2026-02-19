import { useState } from "react";
import { useMacroInsight, useMacroDates, useRegime } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import {
  Globe,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  DollarSign,
} from "lucide-react";

export default function Macro() {
  const [selectedDate, setSelectedDate] = useState<string | undefined>();
  const dates = useMacroDates(30);
  const insight = useMacroInsight(selectedDate);
  const regime = useRegime();

  const data = insight.data;
  const noData = data && "status" in data && data.status === "no_data";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Macro Council</h1>

        {/* Date Selector */}
        {dates.data && dates.data.length > 0 && (
          <select
            value={selectedDate ?? ""}
            onChange={(e) => setSelectedDate(e.target.value || undefined)}
            className="rounded-md border border-border-primary bg-bg-secondary px-3 py-1.5 text-sm text-text-primary"
          >
            <option value="">Latest</option>
            {dates.data.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
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
          {/* Top Summary */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Card>
              <div className="flex items-center gap-3">
                <Globe size={18} className="text-accent-blue" />
                <div>
                  <p className="text-xs text-text-secondary">Sentiment</p>
                  <StatusBadge status={data.sentiment} size="md" />
                </div>
              </div>
            </Card>

            <Card>
              <div className="flex items-center gap-3">
                <TrendingUp size={18} className="text-accent-green" />
                <div>
                  <p className="text-xs text-text-secondary">Position Size</p>
                  <p className="text-lg font-bold">{data.position_size_pct}%</p>
                </div>
              </div>
            </Card>

            <Card>
              <div className="flex items-center gap-3">
                <AlertTriangle size={18} className="text-accent-yellow" />
                <div>
                  <p className="text-xs text-text-secondary">Political Risk</p>
                  <p className="text-sm font-medium capitalize">{data.political_risk_level ?? "N/A"}</p>
                </div>
              </div>
            </Card>

            <Card>
              <div className="flex items-center gap-3">
                <DollarSign size={18} className="text-accent-purple" />
                <div>
                  <p className="text-xs text-text-secondary">Council Cost</p>
                  <p className="text-lg font-bold">
                    ${data.council_cost_usd?.toFixed(3) ?? "0"}
                  </p>
                </div>
              </div>
            </Card>
          </div>

          {/* Market Data */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card title="Market Indicators">
              <div className="space-y-3">
                <MetricRow label="Regime Hint" value={data.regime_hint ?? "-"} />
                <MetricRow label="VIX" value={data.vix_value?.toFixed(1) ?? "-"} sub={data.vix_regime ?? ""} />
                <MetricRow label="USD/KRW" value={data.usd_krw?.toFixed(0) ?? "-"} />
                <MetricRow label="KOSPI" value={data.kospi_index?.toFixed(1) ?? "-"} />
                <MetricRow label="KOSDAQ" value={data.kosdaq_index?.toFixed(1) ?? "-"} />
                <MetricRow label="Stop Loss Adj." value={`${data.stop_loss_adjust_pct}%`} />
              </div>
            </Card>

            <Card title="Current Regime">
              {regime.data ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-text-secondary">Regime</span>
                    <StatusBadge status={regime.data.regime} size="md" />
                  </div>
                  <MetricRow
                    label="Position Mult."
                    value={`${regime.data.position_multiplier.toFixed(2)}x`}
                  />
                  <MetricRow
                    label="Stop Loss Mult."
                    value={`${regime.data.stop_loss_multiplier.toFixed(2)}x`}
                  />
                  <MetricRow
                    label="Risk-Off Level"
                    value={String(regime.data.risk_off_level)}
                  />
                  <MetricRow
                    label="High Volatility"
                    value={regime.data.is_high_volatility ? "Yes" : "No"}
                    color={regime.data.is_high_volatility ? "text-accent-red" : "text-accent-green"}
                  />
                </div>
              ) : (
                <p className="text-sm text-text-muted">No regime data</p>
              )}
            </Card>
          </div>

          {/* Sector Signals */}
          {data.sector_signals && data.sector_signals.length > 0 && (
            <Card title="Sector Signals">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {data.sector_signals.map((s, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 rounded-md border border-border-primary bg-bg-primary p-3"
                  >
                    {s.signal === "favor" ? (
                      <TrendingUp size={16} className="mt-0.5 text-accent-green" />
                    ) : (
                      <TrendingDown size={16} className="mt-0.5 text-accent-red" />
                    )}
                    <div>
                      <p className="text-sm font-medium">{s.sector}</p>
                      <p className="text-xs text-text-secondary">{s.reason}</p>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Favor / Avoid */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {data.sectors_to_favor && (
              <Card title="Sectors to Favor">
                <p className="text-sm text-accent-green">{data.sectors_to_favor}</p>
              </Card>
            )}
            {data.sectors_to_avoid && (
              <Card title="Sectors to Avoid">
                <p className="text-sm text-accent-red">{data.sectors_to_avoid}</p>
              </Card>
            )}
          </div>

          {/* Political Risk */}
          {data.political_risk_summary && (
            <Card title="Political Risk Summary">
              <p className="text-sm text-text-secondary leading-relaxed">
                {data.political_risk_summary}
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function MetricRow({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-text-secondary">{label}</span>
      <div className="text-right">
        <span className={`text-sm font-mono ${color ?? ""}`}>{value}</span>
        {sub && <span className="ml-1 text-xs text-text-muted">({sub})</span>}
      </div>
    </div>
  );
}
