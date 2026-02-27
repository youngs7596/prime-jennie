import { useWatchlistCurrent, useWatchlistHistory } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import { Eye, EyeOff } from "lucide-react";

export default function Scout() {
  const current = useWatchlistCurrent();
  const history = useWatchlistHistory();

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Scout / Watchlist</h1>

      {/* Current Active Watchlist */}
      <Card
        title={
          current.data?.generated_at
            ? `Active Watchlist — ${new Date(current.data.generated_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}`
            : "Active Watchlist"
        }
      >
        {current.isLoading && <LoadingSpinner />}
        {current.data?.status === "no_data" && (
          <p className="py-4 text-sm text-text-muted">No active watchlist</p>
        )}
        {current.data?.stocks && current.data.stocks.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                  <th className="pb-2">#</th>
                  <th className="pb-2">Stock</th>
                  <th className="pb-2 text-right">Hybrid</th>
                  <th className="pb-2 text-right">LLM</th>
                  <th className="pb-2">Tier</th>
                  <th className="pb-2">Risk</th>
                  <th className="pb-2 text-center">Tradable</th>
                </tr>
              </thead>
              <tbody>
                {current.data.stocks.map((s) => (
                  <tr key={s.stock_code} className="border-b border-border-primary/50">
                    <td className="py-2 text-text-muted">{s.rank}</td>
                    <td className="py-2">
                      <span className="font-medium">{s.stock_name}</span>
                      <span className="ml-1 text-xs text-text-muted">{s.stock_code}</span>
                    </td>
                    <td className="py-2 text-right">
                      <ScoreBar value={s.hybrid_score} />
                    </td>
                    <td className="py-2 text-right">
                      <ScoreBar value={s.llm_score} />
                    </td>
                    <td className="py-2">
                      <StatusBadge status={s.trade_tier} />
                    </td>
                    <td className="py-2">
                      {s.risk_tag ? <StatusBadge status={s.risk_tag} /> : <span className="text-text-muted">-</span>}
                    </td>
                    <td className="py-2 text-center">
                      {s.is_tradable ? (
                        <Eye size={14} className="mx-auto text-accent-green" />
                      ) : (
                        <EyeOff size={14} className="mx-auto text-accent-red" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Watchlist History */}
      <Card
        title={
          history.data && history.data.length > 0
            ? `Watchlist History — ${history.data[0].snapshot_date}`
            : "Watchlist History"
        }
      >
        {history.isLoading && <LoadingSpinner />}
        {history.data && history.data.length === 0 && (
          <p className="py-4 text-sm text-text-muted">No history</p>
        )}
        {history.data && history.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                  <th className="pb-2">Date</th>
                  <th className="pb-2">Stock</th>
                  <th className="pb-2">Sector</th>
                  <th className="pb-2 text-right">Hybrid</th>
                  <th className="pb-2 text-right">Quant</th>
                  <th className="pb-2 text-right">LLM</th>
                  <th className="pb-2">Tier</th>
                  <th className="pb-2">Risk</th>
                </tr>
              </thead>
              <tbody>
                {history.data.slice(0, 50).map((s, i) => (
                  <tr key={`${s.stock_code}-${s.snapshot_date}-${i}`} className="border-b border-border-primary/50">
                    <td className="py-2 text-xs text-text-muted">{s.snapshot_date}</td>
                    <td className="py-2">
                      <span className="font-medium">{s.stock_name}</span>
                      <span className="ml-1 text-xs text-text-muted">{s.stock_code}</span>
                    </td>
                    <td className="py-2 text-xs text-text-secondary">{s.sector_group ?? "-"}</td>
                    <td className="py-2 text-right">
                      <ScoreBar value={s.hybrid_score} />
                    </td>
                    <td className="py-2 text-right">
                      <ScoreBar value={s.quant_score} />
                    </td>
                    <td className="py-2 text-right">
                      <ScoreBar value={s.llm_score} />
                    </td>
                    <td className="py-2">
                      <StatusBadge status={s.trade_tier} />
                    </td>
                    <td className="py-2">
                      {s.risk_tag ? <StatusBadge status={s.risk_tag} /> : <span className="text-text-muted">-</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ScoreBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-text-muted">-</span>;

  const color =
    value >= 70
      ? "bg-accent-green"
      : value >= 50
        ? "bg-accent-yellow"
        : "bg-accent-red";

  return (
    <div className="flex items-center justify-end gap-2">
      <div className="h-1.5 w-12 rounded-full bg-bg-tertiary">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <span className="w-8 text-right font-mono text-xs">{value.toFixed(0)}</span>
    </div>
  );
}
