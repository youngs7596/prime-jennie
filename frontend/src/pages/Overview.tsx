import {
  usePortfolioSummary,
  useRegime,
  useRecentTrades,
  useLLMStats,
  usePerformance,
} from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import { TrendingUp, TrendingDown, DollarSign, BarChart3 } from "lucide-react";

function formatKRW(amount: number): string {
  if (Math.abs(amount) >= 1_0000_0000) {
    return `${(amount / 1_0000_0000).toFixed(1)}억`;
  }
  if (Math.abs(amount) >= 1_0000) {
    return `${(amount / 1_0000).toFixed(0)}만`;
  }
  return amount.toLocaleString();
}

export default function Overview() {
  const portfolio = usePortfolioSummary();
  const regime = useRegime();
  const trades = useRecentTrades(7);
  const llm = useLLMStats();
  const perf = usePerformance(30);

  if (portfolio.isLoading) return <LoadingSpinner />;

  const p = portfolio.data;
  const r = regime.data;
  const pf = perf.data;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Dashboard Overview</h1>

      {/* Top Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-accent-blue/15 p-2">
              <DollarSign size={18} className="text-accent-blue" />
            </div>
            <div>
              <p className="text-xs text-text-secondary">Total Asset</p>
              <p className="stat-value text-lg">{p ? formatKRW(p.total_asset) : "-"}</p>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-accent-green/15 p-2">
              <TrendingUp size={18} className="text-accent-green" />
            </div>
            <div>
              <p className="text-xs text-text-secondary">Cash</p>
              <p className="stat-value text-lg">{p ? formatKRW(p.cash_balance) : "-"}</p>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-accent-purple/15 p-2">
              <BarChart3 size={18} className="text-accent-purple" />
            </div>
            <div>
              <p className="text-xs text-text-secondary">Positions</p>
              <p className="stat-value text-lg">{p?.position_count ?? 0}</p>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-accent-yellow/15 p-2">
              <TrendingDown size={18} className="text-accent-yellow" />
            </div>
            <div>
              <p className="text-xs text-text-secondary">Win Rate (30d)</p>
              <p className="stat-value text-lg">
                {pf ? `${(pf.win_rate * 100).toFixed(0)}%` : "-"}
              </p>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Market Regime */}
        <Card title="Market Regime">
          {r ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Current Regime</span>
                <StatusBadge status={r.regime} size="md" />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Position Multiplier</span>
                <span className="text-sm font-mono">{r.position_multiplier.toFixed(2)}x</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Stop Loss Adj.</span>
                <span className="text-sm font-mono">{r.stop_loss_multiplier.toFixed(2)}x</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">High Volatility</span>
                <span className={`text-sm ${r.is_high_volatility ? "text-accent-red" : "text-accent-green"}`}>
                  {r.is_high_volatility ? "Yes" : "No"}
                </span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-text-muted">No data</p>
          )}
        </Card>

        {/* Performance Summary */}
        <Card title="Trading Performance (30d)">
          {pf && pf.total_trades > 0 ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Total Trades</span>
                <span className="text-sm font-mono">{pf.total_trades}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Win / Loss</span>
                <span className="text-sm font-mono">
                  <span className="text-accent-green">{pf.win_trades}W</span>
                  {" / "}
                  <span className="text-accent-red">{pf.loss_trades}L</span>
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Avg Return</span>
                <span className={`text-sm font-mono ${pf.avg_return_pct >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                  {pf.avg_return_pct >= 0 ? "+" : ""}{pf.avg_return_pct}%
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">Total Profit</span>
                <span className={`text-sm font-mono ${pf.total_profit >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                  {formatKRW(pf.total_profit)}
                </span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-text-muted">No trades in period</p>
          )}
        </Card>
      </div>

      {/* LLM Usage */}
      <Card title="LLM Usage (Today)">
        {llm.data ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {Object.entries(llm.data.services).map(([svc, stats]) => (
              <div key={svc} className="rounded-md border border-border-primary bg-bg-primary p-3">
                <p className="text-xs text-text-secondary capitalize">{svc.replace("_", " ")}</p>
                <p className="mt-1 text-lg font-bold">{stats.calls}</p>
                <p className="text-xs text-text-muted">
                  {(stats.tokens_in + stats.tokens_out).toLocaleString()} tokens
                </p>
              </div>
            ))}
            <div className="rounded-md border border-accent-blue/30 bg-accent-blue/5 p-3">
              <p className="text-xs text-accent-blue">Total</p>
              <p className="mt-1 text-lg font-bold">{llm.data.total.calls}</p>
              <p className="text-xs text-text-muted">
                {(llm.data.total.tokens_in + llm.data.total.tokens_out).toLocaleString()} tokens
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-text-muted">No data</p>
        )}
      </Card>

      {/* Recent Trades */}
      <Card title="Recent Trades (7d)">
        {trades.data && trades.data.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                  <th className="pb-2">Stock</th>
                  <th className="pb-2">Type</th>
                  <th className="pb-2 text-right">Price</th>
                  <th className="pb-2 text-right">Qty</th>
                  <th className="pb-2 text-right">P&L</th>
                  <th className="pb-2">Strategy</th>
                  <th className="pb-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.data.slice(0, 10).map((t) => (
                  <tr key={t.id} className="border-b border-border-primary/50">
                    <td className="py-2">
                      <span className="font-medium">{t.stock_name}</span>
                      <span className="ml-1 text-xs text-text-muted">{t.stock_code}</span>
                    </td>
                    <td className="py-2">
                      <span className={t.trade_type === "BUY" ? "text-accent-green" : "text-accent-red"}>
                        {t.trade_type}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">{t.price?.toLocaleString()}</td>
                    <td className="py-2 text-right font-mono">{t.quantity}</td>
                    <td className="py-2 text-right">
                      {t.profit_pct != null ? (
                        <span className={t.profit_pct >= 0 ? "text-accent-green" : "text-accent-red"}>
                          {t.profit_pct >= 0 ? "+" : ""}{t.profit_pct.toFixed(1)}%
                        </span>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td className="py-2 text-xs text-text-muted">{t.strategy_signal ?? "-"}</td>
                    <td className="py-2 text-xs text-text-muted">
                      {t.timestamp ? new Date(t.timestamp).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-text-muted">No trades</p>
        )}
      </Card>
    </div>
  );
}
