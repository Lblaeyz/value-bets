import { useState } from "react";
import { api } from "@/lib/api";
import type { BudgetStatus } from "@/types";

interface PipelineStatusProps {
  budget: BudgetStatus | null;
  loading: boolean;
}

export function PipelineStatus({ budget, loading }: PipelineStatusProps) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function triggerPipeline() {
    setRunning(true);
    setResult(null);
    try {
      const res = await api.admin.runPipeline();
      setResult(res.success ? "Pipeline completed successfully." : "Pipeline finished with errors.");
    } catch (e) {
      setResult(`Error: ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-neutral-300">Pipeline & Budget</h3>
        <button
          onClick={triggerPipeline}
          disabled={running}
          className="rounded-md bg-white/10 px-3 py-1 text-xs font-medium text-neutral-200 transition-colors hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "Running…" : "Run Pipeline"}
        </button>
      </div>

      {loading && (
        <p className="text-xs text-neutral-600">Loading budget…</p>
      )}

      {budget && !loading && (
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <p className="mb-1 text-neutral-500">API-Football today</p>
            <p className="tabular-nums text-neutral-200">
              {budget.api_football_calls_today} / {budget.api_football_daily_limit}
              <span className="ml-1 text-neutral-500">({budget.api_football_remaining} left)</span>
            </p>
          </div>
          <div>
            <p className="mb-1 text-neutral-500">Odds API this month</p>
            <p className="tabular-nums text-neutral-200">
              {budget.odds_api_calls_this_month} / {budget.odds_api_monthly_limit}
              <span className="ml-1 text-neutral-500">({budget.odds_api_remaining} left)</span>
            </p>
          </div>
        </div>
      )}

      {budget?.budget_warning && (
        <p className="mt-2 text-xs font-medium text-yellow-400">⚠ Odds API budget running low</p>
      )}

      {result && (
        <p className={`mt-2 text-xs ${result.startsWith("Error") ? "text-red-400" : "text-green-400"}`}>
          {result}
        </p>
      )}
    </div>
  );
}
