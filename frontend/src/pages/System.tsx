import { useSystemHealth, useLLMStats } from "@/lib/api";
import Card from "@/components/Card";
import StatusBadge from "@/components/StatusBadge";
import LoadingSpinner from "@/components/LoadingSpinner";
import { Activity, Clock } from "lucide-react";

function formatUptime(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export default function System() {
  const health = useSystemHealth();
  const llm = useLLMStats();

  const services = health.data ?? [];
  const healthyCount = services.filter((s) => s.status === "healthy").length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">System Status</h1>
        <div className="flex items-center gap-2">
          <Activity size={14} className={healthyCount === services.length && services.length > 0 ? "text-accent-green" : "text-accent-yellow"} />
          <span className="text-sm text-text-secondary">
            {healthyCount}/{services.length} services healthy
          </span>
        </div>
      </div>

      {/* Service Grid */}
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
                <p className="text-2xl font-bold">{llm.data.total.tokens_in.toLocaleString()}</p>
              </div>
              <div className="rounded-md border border-border-primary bg-bg-primary p-3">
                <p className="text-xs text-text-secondary">Tokens Out</p>
                <p className="text-2xl font-bold">{llm.data.total.tokens_out.toLocaleString()}</p>
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
                        <td className="py-2 text-right font-mono">{stats.tokens_in.toLocaleString()}</td>
                        <td className="py-2 text-right font-mono">{stats.tokens_out.toLocaleString()}</td>
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
