interface ConfidenceBarProps {
  value: number;
  showLabel?: boolean;
}

export function ConfidenceBar({ value, showLabel = true }: ConfidenceBarProps) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "bg-blue-400" : pct >= 60 ? "bg-blue-500" : "bg-blue-700";

  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showLabel && (
        <span className="w-9 text-right text-xs tabular-nums text-neutral-400">
          {pct}%
        </span>
      )}
    </div>
  );
}
