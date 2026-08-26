interface ValueBadgeProps {
  edge: number;
  size?: "sm" | "md";
}

export function ValueBadge({ edge, size = "md" }: ValueBadgeProps) {
  const isPositive = edge > 0;
  const pct = (edge * 100).toFixed(1);

  const base =
    size === "sm"
      ? "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold tabular-nums"
      : "inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm font-bold tabular-nums";

  const color = isPositive
    ? "bg-green-500/15 text-green-400 ring-1 ring-green-500/30"
    : "bg-red-500/15 text-red-400 ring-1 ring-red-500/30";

  return (
    <span className={`${base} ${color}`}>
      {isPositive ? "▲" : "▼"} {isPositive ? "+" : ""}{pct}%
    </span>
  );
}
