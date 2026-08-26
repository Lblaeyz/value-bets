interface NoBetsTodayProps {
  matchesAnalyzed: number;
  date: string;
}

export function NoBetsToday({ matchesAnalyzed, date }: NoBetsTodayProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-white/8 bg-[#1a1a1a] py-16 text-center">
      <div className="mb-4 text-4xl">📊</div>
      <h2 className="mb-2 text-xl font-semibold text-neutral-200">
        No value bets today
      </h2>
      <p className="mb-1 text-sm text-neutral-500">
        The model analyzed{" "}
        <span className="font-medium text-neutral-300">{matchesAnalyzed}</span>{" "}
        {matchesAnalyzed === 1 ? "match" : "matches"} for {date}
      </p>
      <p className="text-sm text-neutral-600">
        No fixtures met the minimum value edge and confidence thresholds.
      </p>
    </div>
  );
}
