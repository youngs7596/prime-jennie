import { useState } from "react";
import {
  useSystemHealth,
  useLLMStats,
  useAirflowDags,
  triggerDag,
  useLogServices,
  useLogs,
} from "@/lib/api";
import type { AirflowDag, LogEntry } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import { Activity, Clock, Play, ScrollText, Workflow } from "lucide-react";

type Tab = "services" | "workflows" | "logs";

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
  const llm = useLLMStats();

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

      {/* LLM Usage */}
      <Card title="LLM Usage (Today)">
        {llm.isLoading && <LoadingSpinner />}
        {llm.data && (
          <>
            <div className="mb-4 grid grid-cols-3 gap-4">
              <div className="rounded-md border border-border-primary bg-bg-primary p-3">
                <p className="text-xs text-text-secondary">Total Calls</p>
                <p className="text-2xl font-bold">{llm.data.total.calls}</p>
              </div>
              <div className="rounded-md border border-border-primary bg-bg-primary p-3">
                <p className="text-xs text-text-secondary">Tokens In</p>
                <p className="text-2xl font-bold">
                  {llm.data.total.tokens_in.toLocaleString()}
                </p>
              </div>
              <div className="rounded-md border border-border-primary bg-bg-primary p-3">
                <p className="text-xs text-text-secondary">Tokens Out</p>
                <p className="text-2xl font-bold">
                  {llm.data.total.tokens_out.toLocaleString()}
                </p>
              </div>
            </div>

            {Object.keys(llm.data.services).length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-primary text-left text-xs text-text-secondary">
                      <th className="pb-2">Service</th>
                      <th className="pb-2 text-right">Calls</th>
                      <th className="pb-2 text-right">Tokens In</th>
                      <th className="pb-2 text-right">Tokens Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(llm.data.services).map(([svc, stats]) => (
                      <tr key={svc} className="border-b border-border-primary/50">
                        <td className="py-2 capitalize">{svc.replace("_", " ")}</td>
                        <td className="py-2 text-right font-mono">{stats.calls}</td>
                        <td className="py-2 text-right font-mono">
                          {stats.tokens_in.toLocaleString()}
                        </td>
                        <td className="py-2 text-right font-mono">
                          {stats.tokens_out.toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Card>
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

  const handleTrigger = async (dag: AirflowDag) => {
    setTriggering(dag.dag_id);
    try {
      await triggerDag(dag.dag_id);
    } catch {
      // error silently — UI will show updated state on next refetch
    } finally {
      setTriggering(null);
    }
  };

  if (isLoading) return <LoadingSpinner />;

  if (!dags || dags.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-text-muted">
        No active DAGs found
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {dags.map((dag) => (
        <div key={dag.dag_id} className="card">
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
      {tab === "logs" && <LogsTab />}
    </div>
  );
}
