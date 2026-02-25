import { useState } from "react";
import { useLivePositions, useAssetHistory, usePerformance } from "@/lib/api";
import Card from "@/components/Card";
import LoadingSpinner from "@/components/LoadingSpinner";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
} from "recharts";

function formatKRW(amount: number): string {
  if (Math.abs(amount) >= 1_0000_0000) {
    return `${(amount / 1_0000_0000).toFixed(1)}억`;
  }
  if (Math.abs(amount) >= 1_0000) {
    return `${(amount / 1_0000).toFixed(0)}만`;
  }
  return amount.toLocaleString();
}

function profitColor(pct: number | null): string {
  if (pct == null) return "text-text-muted";
  if (pct > 0) return "text-accent-green";
  if (pct < 0) return "text-accent-red";
  return "text-text-secondary";
}

type Tab = "positions" | "history";

export default function Portfolio() {
  const [tab, setTab] = useState<Tab>("positions");
  const [days, setDays] = useState(30);
  const live = useLivePositions();
  const positions = live.data?.positions;
  const updatedAt = live.data?.updated_at;
  const history = useAssetHistory(days);
  const perf = usePerformance(days);

  // 총 평가손익 계산
  const totalProfit = positions?.reduce((sum, p) => {
    if (p.current_value != null) return sum + (p.current_value - p.total_buy_amount);
    return sum;
  }, 0) ?? 0;
  const totalEval = positions?.reduce((sum, p) => sum + (p.current_value ?? p.total_buy_amount), 0) ?? 0;
  const totalBuy = positions?.reduce((sum, p) => sum + p.total_buy_amount, 0) ?? 0;
  const totalProfitPct = totalBuy > 0 ? (totalProfit / totalBuy) * 100 : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Portfolio</h1>
        {updatedAt && (
          <span className="text-xs text-text-muted">
            {new Date(updatedAt).toLocaleTimeString("ko-KR")} updated
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg bg-bg-secondary p-1">
        {(["positions", "history"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-4 py-1.5 text-sm transition-colors ${
              tab === t
                ? "bg-bg-tertiary text-text-primary"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {t === "positions" ? "Positions" : "Asset History"}
          </button>
        ))}
      </div>

      {tab === "positions" && (
        <>
          {live.isLoading && <LoadingSpinner />}
          {positions && positions.length === 0 && (
            <p className="py-8 text-center text-sm text-text-muted">No positions</p>
          )}
          {positions && positions.length > 0 && (
            <>
              {/* Summary bar */}
              <div className="flex items-center gap-6 rounded-lg bg-bg-secondary px-4 py-3 text-sm">
                <div>
                  <span className="text-text-secondary">Holdings </span>
                  <span className="font-bold">{positions.length}</span>
                </div>
                <div>
                  <span className="text-text-secondary">Eval </span>
                  <span className="font-bold">{formatKRW(totalEval)}</span>
                </div>
                <div>
                  <span className="text-text-secondary">P&L </span>
                  <span className={`font-bold ${profitColor(totalProfit)}`}>
                    {totalProfit >= 0 ? "+" : ""}{formatKRW(totalProfit)}
                    <span className="ml-1 text-xs">
                      ({totalProfitPct >= 0 ? "+" : ""}{totalProfitPct.toFixed(1)}%)
                    </span>
                  </span>
                </div>
              </div>

              <Card>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                        <th className="pb-2">Stock</th>
                        <th className="pb-2">Sector</th>
                        <th className="pb-2 text-right">Qty</th>
                        <th className="pb-2 text-right">Avg Price</th>
                        <th className="pb-2 text-right">Cur Price</th>
                        <th className="pb-2 text-right">P&L</th>
                        <th className="pb-2 text-right">Eval</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions
                        .slice()
                        .sort((a, b) => (b.profit_pct ?? 0) - (a.profit_pct ?? 0))
                        .map((p) => (
                        <tr key={p.stock_code} className="border-b border-border-primary/50">
                          <td className="py-2.5">
                            <span className="font-medium">{p.stock_name}</span>
                            <span className="ml-1 text-xs text-text-muted">{p.stock_code}</span>
                          </td>
                          <td className="py-2.5 text-xs text-text-secondary">
                            {p.sector_group ?? "-"}
                          </td>
                          <td className="py-2.5 text-right font-mono">{p.quantity}</td>
                          <td className="py-2.5 text-right font-mono">
                            {p.average_buy_price.toLocaleString()}
                          </td>
                          <td className="py-2.5 text-right font-mono">
                            {p.current_price != null && p.current_price > 0
                              ? p.current_price.toLocaleString()
                              : "-"}
                          </td>
                          <td className={`py-2.5 text-right font-mono font-medium ${profitColor(p.profit_pct)}`}>
                            {p.profit_pct != null
                              ? `${p.profit_pct >= 0 ? "+" : ""}${p.profit_pct.toFixed(1)}%`
                              : "-"}
                          </td>
                          <td className="py-2.5 text-right font-mono">
                            {p.current_value != null ? formatKRW(p.current_value) : formatKRW(p.total_buy_amount)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )}
        </>
      )}

      {tab === "history" && (
        <>
          {/* Period selector */}
          <div className="flex gap-2">
            {[7, 14, 30, 60, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded-md px-3 py-1 text-xs transition-colors ${
                  days === d
                    ? "bg-accent-blue text-white"
                    : "bg-bg-secondary text-text-secondary hover:bg-bg-tertiary"
                }`}
              >
                {d}d
              </button>
            ))}
          </div>

          {history.isLoading && <LoadingSpinner />}

          {/* Asset Chart */}
          {history.data && history.data.length > 0 && (
            <Card title="Total Asset">
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={history.data}>
                    <defs>
                      <linearGradient id="assetGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#58A6FF" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#58A6FF" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#30363D" />
                    <XAxis
                      dataKey="snapshot_date"
                      tickFormatter={(d: string) => d.slice(5)}
                      stroke="#484F58"
                      fontSize={11}
                    />
                    <YAxis
                      tickFormatter={(v: number) => formatKRW(v)}
                      stroke="#484F58"
                      fontSize={11}
                      width={60}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#161B22",
                        border: "1px solid #30363D",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(v: number) => [v.toLocaleString() + "원", "Total Asset"]}
                      labelFormatter={(l: string) => l}
                    />
                    <Area
                      type="monotone"
                      dataKey="total_asset"
                      stroke="#58A6FF"
                      fill="url(#assetGrad)"
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}

          {/* Profit Chart */}
          {history.data && history.data.length > 0 && (
            <Card title="Cumulative P&L">
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={history.data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#30363D" />
                    <XAxis
                      dataKey="snapshot_date"
                      tickFormatter={(d: string) => d.slice(5)}
                      stroke="#484F58"
                      fontSize={11}
                    />
                    <YAxis
                      tickFormatter={(v: number) => formatKRW(v)}
                      stroke="#484F58"
                      fontSize={11}
                      width={60}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#161B22",
                        border: "1px solid #30363D",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(v: number) => [v.toLocaleString() + "원"]}
                    />
                    <Line
                      type="monotone"
                      dataKey="total_profit_loss"
                      stroke="#3FB950"
                      strokeWidth={2}
                      dot={false}
                      name="Total P/L"
                    />
                    <Line
                      type="monotone"
                      dataKey="realized_profit_loss"
                      stroke="#F0883E"
                      strokeWidth={1.5}
                      dot={false}
                      strokeDasharray="4 4"
                      name="Realized P/L"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}

          {/* Performance Summary */}
          {perf.data && perf.data.total_trades > 0 && (
            <Card title="Performance Summary">
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div>
                  <p className="text-xs text-text-secondary">Total Trades</p>
                  <p className="text-xl font-bold">{perf.data.total_trades}</p>
                </div>
                <div>
                  <p className="text-xs text-text-secondary">Win Rate</p>
                  <p className="text-xl font-bold">
                    {(perf.data.win_rate * 100).toFixed(0)}%
                  </p>
                </div>
                <div>
                  <p className="text-xs text-text-secondary">Avg Return</p>
                  <p className={`text-xl font-bold ${perf.data.avg_return_pct >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                    {perf.data.avg_return_pct >= 0 ? "+" : ""}{perf.data.avg_return_pct}%
                  </p>
                </div>
                <div>
                  <p className="text-xs text-text-secondary">Total Profit</p>
                  <p className={`text-xl font-bold ${perf.data.total_profit >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                    {formatKRW(perf.data.total_profit)}
                  </p>
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
