import axios from "axios";
import { useQuery } from "@tanstack/react-query";

const api = axios.create({
  baseURL: "/api",
  timeout: 10_000,
});

/* ── Type Definitions ──────────────────────────────────── */

export interface Position {
  stock_code: string;
  stock_name: string;
  quantity: number;
  average_buy_price: number;
  total_buy_amount: number;
  current_price: number | null;
  current_value: number | null;
  profit_pct: number | null;
  sector_group: string | null;
  high_watermark: number | null;
  stop_loss_price: number | null;
}

export interface PortfolioState {
  positions: Position[];
  cash_balance: number;
  total_asset: number;
  stock_eval_amount: number;
  position_count: number;
  timestamp: string;
}

export interface DailySnapshot {
  snapshot_date: string;
  total_asset: number;
  cash_balance: number;
  stock_eval_amount: number;
  total_profit_loss: number;
  realized_profit_loss: number;
}

export interface PerformanceSummary {
  total_trades: number;
  win_trades: number;
  loss_trades: number;
  win_rate: number;
  avg_return_pct: number;
  total_profit: number;
}

export interface RegimeResponse {
  regime: string;
  position_multiplier: number;
  stop_loss_multiplier: number;
  risk_off_level: number;
  is_high_volatility: boolean;
}

export interface MacroInsight {
  insight_date: string;
  sentiment: string;
  sentiment_score: number;
  regime_hint: string;
  position_size_pct: number;
  stop_loss_adjust_pct: number;
  political_risk_level: string;
  political_risk_summary: string | null;
  vix_value: number | null;
  vix_regime: string | null;
  usd_krw: number | null;
  kospi_index: number | null;
  kosdaq_index: number | null;
  sectors_to_favor: string | null;
  sectors_to_avoid: string | null;
  sector_signals: Array<{ sector_group: string; signal: string; confidence?: string; reasoning?: string }>;
  council_cost_usd: number | null;
  trading_reasoning: string | null;
  council_consensus: string | null;
  strategies_to_favor: string[];
  strategies_to_avoid: string[];
  risk_factors: Array<{ name: string; severity: string }>;
  opportunity_factors: string[];
  kospi_change_pct: number | null;
  kosdaq_change_pct: number | null;
  kospi_foreign_net: number | null;
  kospi_institutional_net: number | null;
  kospi_retail_net: number | null;
  data_completeness_pct: number | null;
}

export interface WatchlistEntry {
  snapshot_date: string;
  stock_code: string;
  stock_name: string;
  llm_score: number | null;
  hybrid_score: number | null;
  is_tradable: boolean;
  trade_tier: string;
  risk_tag: string | null;
  rank: number;
  quant_score: number | null;
  sector_group: string | null;
  market_regime: string | null;
}

export interface TradeRecord {
  id: number;
  stock_code: string;
  stock_name: string;
  trade_type: string;
  quantity: number;
  price: number;
  total_amount: number;
  reason: string | null;
  strategy_signal: string | null;
  market_regime: string | null;
  llm_score: number | null;
  hybrid_score: number | null;
  trade_tier: string | null;
  profit_pct: number | null;
  profit_amount: number | null;
  holding_days: number | null;
  timestamp: string | null;
}

export interface ServiceStatus {
  name: string;
  port: number;
  status: string;
  version: string | null;
  uptime_seconds: number | null;
  message: string | null;
}

export interface LLMStats {
  date: string;
  services: Record<string, { calls: number; tokens_in: number; tokens_out: number }>;
  total: { calls: number; tokens_in: number; tokens_out: number };
}

export interface AirflowDag {
  dag_id: string;
  description: string | null;
  schedule_interval: string | null;
  next_dagrun: string | null;
  last_run_state: string;
  last_run_date: string | null;
}

export interface LogEntry {
  timestamp: string;
  message: string;
}

/* ── React Query Hooks ─────────────────────────────────── */

export function usePortfolioSummary() {
  return useQuery<PortfolioState>({
    queryKey: ["portfolio", "summary"],
    queryFn: () => api.get("/portfolio/summary").then((r) => r.data),
  });
}

export interface LivePositionsResponse {
  positions: Position[];
  updated_at: string | null;
}

export function useLivePositions() {
  return useQuery<LivePositionsResponse>({
    queryKey: ["portfolio", "live"],
    queryFn: () => api.get("/portfolio/live").then((r) => r.data),
    refetchInterval: 10_000,
  });
}

export function usePositions() {
  return useQuery<Position[]>({
    queryKey: ["portfolio", "positions"],
    queryFn: () => api.get("/portfolio/positions").then((r) => r.data),
  });
}

export function useAssetHistory(days = 30) {
  return useQuery<DailySnapshot[]>({
    queryKey: ["portfolio", "history", days],
    queryFn: () => api.get(`/portfolio/history?days=${days}`).then((r) => r.data),
  });
}

export function usePerformance(days = 30) {
  return useQuery<PerformanceSummary>({
    queryKey: ["portfolio", "performance", days],
    queryFn: () => api.get(`/portfolio/performance?days=${days}`).then((r) => r.data),
  });
}

export function useMacroInsight(targetDate?: string) {
  return useQuery<MacroInsight>({
    queryKey: ["macro", "insight", targetDate],
    queryFn: () =>
      api
        .get("/macro/insight", { params: targetDate ? { target_date: targetDate } : {} })
        .then((r) => r.data),
  });
}

export function useRegime() {
  return useQuery<RegimeResponse>({
    queryKey: ["macro", "regime"],
    queryFn: () => api.get("/macro/regime").then((r) => r.data),
  });
}

export function useMacroDates(limit = 30) {
  return useQuery<string[]>({
    queryKey: ["macro", "dates", limit],
    queryFn: () => api.get(`/macro/dates?limit=${limit}`).then((r) => r.data),
  });
}

export function useWatchlistCurrent() {
  return useQuery<{ stocks?: WatchlistEntry[]; status?: string; generated_at?: string; market_regime?: string }>({
    queryKey: ["watchlist", "current"],
    queryFn: () => api.get("/watchlist/current").then((r) => r.data),
  });
}

export function useWatchlistHistory() {
  return useQuery<WatchlistEntry[]>({
    queryKey: ["watchlist", "history"],
    queryFn: () => api.get("/watchlist/history").then((r) => r.data),
  });
}

export function useRecentTrades(days = 7) {
  return useQuery<TradeRecord[]>({
    queryKey: ["trades", "recent", days],
    queryFn: () => api.get(`/trades/recent?days=${days}`).then((r) => r.data),
  });
}

export function useLLMStats(targetDate?: string) {
  return useQuery<LLMStats>({
    queryKey: ["llm", "stats", targetDate],
    queryFn: () => {
      const url = targetDate ? `/llm/stats/${targetDate}` : "/llm/stats";
      return api.get(url).then((r) => r.data);
    },
  });
}

export function useSystemHealth() {
  return useQuery<ServiceStatus[]>({
    queryKey: ["system", "health"],
    queryFn: () => api.get("/system/health").then((r) => r.data),
    refetchInterval: 30_000,
  });
}

export function useAirflowDags() {
  return useQuery<AirflowDag[]>({
    queryKey: ["airflow", "dags"],
    queryFn: () => api.get("/airflow/dags").then((r) => r.data),
    refetchInterval: (query) => {
      const dags = query.state.data;
      const hasActive = dags?.some((d) => d.last_run_state === "running" || d.last_run_state === "queued");
      return hasActive ? 5_000 : 60_000;
    },
  });
}

export async function triggerDag(dagId: string) {
  return api.post(`/airflow/dags/${dagId}/trigger`).then((r) => r.data);
}

export function useLogServices() {
  return useQuery<string[]>({
    queryKey: ["logs", "services"],
    queryFn: () => api.get("/logs/services").then((r) => r.data.services),
  });
}

export function useLogs(service: string | null, minutes: number) {
  const start = service ? Math.floor((Date.now() - minutes * 60_000) * 1e6) : 0;
  const end = service ? Math.floor(Date.now() * 1e6) : 0;

  return useQuery<LogEntry[]>({
    queryKey: ["logs", "stream", service, minutes],
    queryFn: () =>
      api
        .get("/logs/stream", {
          params: { service, limit: 500, start, end },
        })
        .then((r) => r.data.logs),
    enabled: !!service,
    refetchInterval: 15_000,
  });
}
