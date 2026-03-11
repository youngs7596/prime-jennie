import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useSystemHealth,
  useLLMStats,
  useLLMMonthlyStats,
  useLLMFeatures,
  useAirflowDags,
  triggerDag,
  useLogServices,
  useLogs,
} from "@/lib/api";
import type { AirflowDag, LogEntry } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import { Activity, Brain, Clock, Play, ScrollText, Workflow } from "lucide-react";

type Tab = "services" | "workflows" | "llm" | "logs";

function formatUptime(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

/* ── Services Tab (기존 내용) ─────────────────────────── */

function ServicesTab() {
  const health = useSystemHealth();

  const services = health.data ?? [];
  const healthyCount = services.filter((s) => s.status === "healthy").length;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Activity
          size={14}
          className={
            healthyCount === services.length && services.length > 0
              ? "text-accent-green"
              : "text-accent-yellow"
          }
        />
        <span className="text-sm text-text-secondary">
          {healthyCount}/{services.length} services healthy
        </span>
      </div>

      {health.isLoading && <LoadingSpinner />}
      {services.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {services.map((svc) => (
            <div
              key={svc.name}
              className={`card ${
                svc.status === "healthy"
                  ? "border-accent-green/20"
                  : svc.status === "unhealthy"
                    ? "border-accent-yellow/20"
                    : "border-accent-red/20"
              }`}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">{svc.name}</h3>
                <StatusBadge status={svc.status} />
              </div>
              <div className="mt-3 space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-text-muted">Port</span>
                  <span className="font-mono text-text-secondary">{svc.port}</span>
                </div>
                {svc.version && (
                  <div className="flex justify-between text-xs">
                    <span className="text-text-muted">Version</span>
                    <span className="font-mono text-text-secondary">{svc.version}</span>
                  </div>
                )}
                <div className="flex justify-between text-xs">
                  <span className="text-text-muted">Uptime</span>
                  <span className="flex items-center gap-1 font-mono text-text-secondary">
                    <Clock size={10} />
                    {formatUptime(svc.uptime_seconds)}
                  </span>
                </div>
                {svc.message && (
                  <p className="mt-1 text-xs text-accent-red">{svc.message}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Workflows Tab ────────────────────────────────────── */

const STATE_COLORS: Record<string, string> = {
  success: "text-accent-green",
  failed: "text-accent-red",
  running: "text-accent-blue",
  queued: "text-accent-yellow",
  unknown: "text-text-muted",
};

function WorkflowsTab() {
  const { data: dags, isLoading } = useAirflowDags();
  const [triggering, setTriggering] = useState<string | null>(null);
  const qc = useQueryClient();

  const handleTrigger = async (dag: AirflowDag) => {
    setTriggering(dag.dag_id);
    try {
      await triggerDag(dag.dag_id);
      // 트리거 후 즉시 refetch → 5초 폴링 전환
      await qc.invalidateQueries({ queryKey: ["airflow", "dags"] });
    } catch {
      // error silently — UI will show updated state on next refetch
    } finally {
      setTriggering(null);
    }
  };

  const hasActive = dags?.some((d) => d.last_run_state === "running" || d.last_run_state === "queued");

  if (isLoading) return <LoadingSpinner />;

  if (!dags || dags.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-text-muted">
        No active DAGs found
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {hasActive && (
        <div className="flex items-center gap-2 text-sm text-accent-blue">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent-blue" />
          실행 중 — 5초마다 갱신
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {dags.map((dag) => (
        <div key={dag.dag_id} className={`card ${dag.last_run_state === "running" ? "border-accent-blue/30" : dag.last_run_state === "failed" ? "border-accent-red/20" : ""}`}>
          <div className="flex items-start justify-between">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-medium">{dag.dag_id}</h3>
              {dag.description && (
                <p className="mt-0.5 truncate text-xs text-text-muted">
                  {dag.description}
                </p>
              )}
            </div>
            <button
              onClick={() => handleTrigger(dag)}
              disabled={triggering === dag.dag_id}
              className="ml-2 flex-shrink-0 rounded-md bg-accent-blue/10 p-1.5 text-accent-blue transition-colors hover:bg-accent-blue/20 disabled:opacity-50"
              title="Trigger DAG"
            >
              <Play size={14} />
            </button>
          </div>
          <div className="mt-3 space-y-1.5">
            {dag.schedule_interval && (
              <div className="flex justify-between text-xs">
                <span className="text-text-muted">Schedule</span>
                <span className="font-mono text-text-secondary">
                  {dag.schedule_interval}
                </span>
              </div>
            )}
            <div className="flex justify-between text-xs">
              <span className="text-text-muted">Last Run</span>
              <span
                className={`font-medium ${STATE_COLORS[dag.last_run_state] ?? "text-text-secondary"}`}
              >
                {dag.last_run_state}
              </span>
            </div>
            {dag.last_run_date && (
              <div className="flex justify-between text-xs">
                <span className="text-text-muted">Run Date</span>
                <span className="font-mono text-text-secondary">
                  {dag.last_run_date.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            )}
            {dag.next_dagrun && (
              <div className="flex justify-between text-xs">
                <span className="text-text-muted">Next Run</span>
                <span className="font-mono text-text-secondary">
                  {dag.next_dagrun.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            )}
          </div>
        </div>
      ))}
      </div>
    </div>
  );
}

/* ── LLM Tab ──────────────────────────────────────────── */

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

function LLMTab() {
  const features = useLLMFeatures();
  const daily = useLLMStats();
  const monthly = useLLMMonthlyStats();

  return (
    <div className="space-y-6">
      {/* 요약 카드 — 당일 / 월간 */}
      <div className="grid grid-cols-2 gap-4">
        <Card title={`당일 (${daily.data?.date ?? "-"})`}>
          {daily.isLoading && <LoadingSpinner />}
          {daily.data && (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <p className="text-xs text-text-secondary">호출</p>
                <p className="text-2xl font-bold">{daily.data.total.calls}</p>
              </div>
              <div>
                <p className="text-xs text-text-secondary">입력 토큰</p>
                <p className="text-2xl font-bold">
                  {formatTokens(daily.data.total.tokens_in)}
                </p>
              </div>
              <div>
                <p className="text-xs text-text-secondary">출력 토큰</p>
                <p className="text-2xl font-bold">
                  {formatTokens(daily.data.total.tokens_out)}
                </p>
              </div>
            </div>
          )}
        </Card>
        <Card title={`월간 (${monthly.data?.month ?? "-"})`}>
          {monthly.isLoading && <LoadingSpinner />}
          {monthly.data && (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <p className="text-xs text-text-secondary">호출</p>
                <p className="text-2xl font-bold">{monthly.data.total.calls}</p>
              </div>
              <div>
                <p className="text-xs text-text-secondary">입력 토큰</p>
                <p className="text-2xl font-bold">
                  {formatTokens(monthly.data.total.tokens_in)}
                </p>
              </div>
              <div>
                <p className="text-xs text-text-secondary">출력 토큰</p>
                <p className="text-2xl font-bold">
                  {formatTokens(monthly.data.total.tokens_out)}
                </p>
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* 기능별 LLM 매핑 + 사용량 통합 테이블 */}
      <Card title="기능별 LLM 사용량">
        {(features.isLoading || daily.isLoading || monthly.isLoading) && (
          <LoadingSpinner />
        )}
        {features.data && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                  <th className="pb-2">기능</th>
                  <th className="pb-2">Provider</th>
                  <th className="pb-2">모델</th>
                  <th className="pb-2">실행 주기</th>
                  <th className="pb-2 text-right">당일 호출</th>
                  <th className="pb-2 text-right">당일 토큰</th>
                  <th className="pb-2 text-right">월간 호출</th>
                  <th className="pb-2 text-right">월간 토큰</th>
                </tr>
              </thead>
              <tbody>
                {features.data.map((feat) => {
                  const ds = daily.data?.services[feat.service];
                  const ms = monthly.data?.services[feat.service];
                  return (
                    <tr
                      key={feat.service}
                      className="border-b border-border-primary/50"
                    >
                      <td className="py-2 font-medium">{feat.name}</td>
                      <td className="py-2 text-text-secondary">{feat.provider}</td>
                      <td className="py-2 font-mono text-xs text-text-muted">
                        {feat.model}
                      </td>
                      <td className="py-2 text-xs text-text-muted">
                        {feat.frequency}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {ds?.calls ?? 0}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {formatTokens((ds?.tokens_in ?? 0) + (ds?.tokens_out ?? 0))}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {ms?.calls ?? 0}
                      </td>
                      <td className="py-2 text-right font-mono">
                        {formatTokens((ms?.tokens_in ?? 0) + (ms?.tokens_out ?? 0))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-border-primary font-medium">
                  <td className="pt-2" colSpan={4}>
                    합계
                  </td>
                  <td className="pt-2 text-right font-mono">
                    {daily.data?.total.calls ?? 0}
                  </td>
                  <td className="pt-2 text-right font-mono">
                    {formatTokens(
                      (daily.data?.total.tokens_in ?? 0) +
                        (daily.data?.total.tokens_out ?? 0),
                    )}
                  </td>
                  <td className="pt-2 text-right font-mono">
                    {monthly.data?.total.calls ?? 0}
                  </td>
                  <td className="pt-2 text-right font-mono">
                    {formatTokens(
                      (monthly.data?.total.tokens_in ?? 0) +
                        (monthly.data?.total.tokens_out ?? 0),
                    )}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ── Logs Tab ─────────────────────────────────────────── */

const TIME_RANGES = [
  { label: "5m", minutes: 5 },
  { label: "30m", minutes: 30 },
  { label: "1h", minutes: 60 },
  { label: "6h", minutes: 360 },
] as const;

function formatLogTs(nsTimestamp: string): string {
  const ms = Number(nsTimestamp) / 1e6;
  const d = new Date(ms);
  return d.toLocaleTimeString("ko-KR", { hour12: false });
}

function LogsTab() {
  const { data: services } = useLogServices();
  const [service, setService] = useState<string | null>(null);
  const [minutes, setMinutes] = useState(30);
  const { data: logs, isLoading } = useLogs(service, minutes);

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={service ?? ""}
          onChange={(e) => setService(e.target.value || null)}
          className="rounded-md border border-border-primary bg-bg-secondary px-3 py-1.5 text-sm text-text-primary"
        >
          <option value="">Select service...</option>
          {services?.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <div className="flex gap-1">
          {TIME_RANGES.map((r) => (
            <button
              key={r.label}
              onClick={() => setMinutes(r.minutes)}
              className={`rounded-md px-3 py-1 text-xs transition-colors ${
                minutes === r.minutes
                  ? "bg-accent-blue text-white"
                  : "bg-bg-secondary text-text-secondary hover:bg-bg-tertiary"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Log Viewer */}
      {!service && (
        <p className="py-8 text-center text-sm text-text-muted">
          Select a service to view logs
        </p>
      )}
      {service && isLoading && <LoadingSpinner />}
      {service && logs && (
        <div className="max-h-[600px] overflow-auto rounded-lg border border-border-primary bg-bg-primary p-3">
          {logs.length === 0 ? (
            <p className="py-4 text-center text-sm text-text-muted">
              No logs found for this time range
            </p>
          ) : (
            <pre className="space-y-px text-xs leading-relaxed">
              {logs.map((entry: LogEntry, i: number) => (
                <div key={i} className="flex gap-2 hover:bg-bg-secondary/50">
                  <span className="flex-shrink-0 text-text-muted">
                    {formatLogTs(entry.timestamp)}
                  </span>
                  <span className="whitespace-pre-wrap break-all text-text-secondary">
                    {entry.message}
                  </span>
                </div>
              ))}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────── */

const TABS: { key: Tab; label: string; icon: typeof Activity }[] = [
  { key: "services", label: "Services", icon: Activity },
  { key: "workflows", label: "Workflows", icon: Workflow },
  { key: "llm", label: "LLM", icon: Brain },
  { key: "logs", label: "Logs", icon: ScrollText },
];

export default function System() {
  const [tab, setTab] = useState<Tab>("services");

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">System Status</h1>

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg bg-bg-secondary p-1">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 rounded-md px-4 py-1.5 text-sm transition-colors ${
                tab === t.key
                  ? "bg-bg-tertiary text-text-primary"
                  : "text-text-secondary hover:text-text-primary"
              }`}
            >
              <Icon size={14} />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "services" && <ServicesTab />}
      {tab === "workflows" && <WorkflowsTab />}
      {tab === "llm" && <LLMTab />}
      {tab === "logs" && <LogsTab />}
    </div>
  );
}
