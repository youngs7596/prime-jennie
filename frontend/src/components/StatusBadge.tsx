interface StatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const STATUS_COLORS: Record<string, string> = {
  healthy: "bg-accent-green/15 text-accent-green",
  unhealthy: "bg-accent-yellow/15 text-accent-yellow",
  unreachable: "bg-accent-red/15 text-accent-red",
  // Market regimes
  STRONG_BULL: "bg-accent-green/15 text-accent-green",
  BULL: "bg-accent-green/15 text-accent-green",
  SIDEWAYS: "bg-accent-yellow/15 text-accent-yellow",
  BEAR: "bg-accent-red/15 text-accent-red",
  STRONG_BEAR: "bg-accent-red/15 text-accent-red",
  // Sentiments
  bullish: "bg-accent-green/15 text-accent-green",
  neutral_to_bullish: "bg-accent-green/15 text-accent-green",
  neutral: "bg-accent-yellow/15 text-accent-yellow",
  neutral_to_bearish: "bg-accent-orange/15 text-accent-orange",
  bearish: "bg-accent-red/15 text-accent-red",
  // Trade tiers
  TIER_1: "bg-accent-green/15 text-accent-green",
  TIER_2: "bg-accent-blue/15 text-accent-blue",
  TIER_3: "bg-accent-yellow/15 text-accent-yellow",
  BLOCKED: "bg-accent-red/15 text-accent-red",
  // Risk tags
  BULLISH: "bg-accent-green/15 text-accent-green",
  NEUTRAL: "bg-accent-yellow/15 text-accent-yellow",
  CAUTION: "bg-accent-orange/15 text-accent-orange",
  DISTRIBUTION_RISK: "bg-accent-red/15 text-accent-red",
};

const LABELS: Record<string, string> = {
  STRONG_BULL: "Strong Bull",
  STRONG_BEAR: "Strong Bear",
  neutral_to_bullish: "Neutral-Bullish",
  neutral_to_bearish: "Neutral-Bearish",
  DISTRIBUTION_RISK: "Dist. Risk",
};

export default function StatusBadge({ status, size = "sm" }: StatusBadgeProps) {
  const colorClass = STATUS_COLORS[status] ?? "bg-bg-tertiary text-text-secondary";
  const label = LABELS[status] ?? status;
  const sizeClass = size === "sm" ? "text-xs px-2 py-0.5" : "text-sm px-3 py-1";

  return (
    <span className={`badge ${colorClass} ${sizeClass}`}>
      {label}
    </span>
  );
}
