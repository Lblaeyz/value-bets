import { useEffect, useState } from "react";
import { MatchCard } from "@/components/MatchCard";
import { LeagueFilter } from "@/components/LeagueFilter";
import { NoBetsToday } from "@/components/NoBetsToday";
import { PipelineStatus } from "@/components/PipelineStatus";
import { api } from "@/lib/api";
import type { TodayResponse, BudgetStatus } from "@/types";

function todayLabel() {
  return new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function Dashboard() {
  const [data, setData] = useState<TodayResponse | null>(null);
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [budgetLoading, setBudgetLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [leagueFilter, setLeagueFilter] = useState("All");

  useEffect(() => {
    api.matches.today()
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));

    api.admin.budget()
      .then(setBudget)
      .catch(() => null)
      .finally(() => setBudgetLoading(false));
  }, []);

  const fixtures = data?.data ?? [];

  const leagues = [...new Set(fixtures.map((f) => f.league.name))].sort();

  const filtered =
    leagueFilter === "All"
      ? fixtures
      : fixtures.filter((f) => f.league.name === leagueFilter);

  const totalAnalyzed = data?.meta?.total_fixtures ?? 0;

  return (
    <div className="min-h-screen bg-[#0f0f0f] px-4 py-8">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-100">
              Value Bets
            </h1>
            <p className="mt-0.5 text-sm text-neutral-500">{todayLabel()}</p>
          </div>
          <div className="w-full sm:w-72">
            <PipelineStatus budget={budget} loading={budgetLoading} />
          </div>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-24">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          </div>
        )}

        {error && !loading && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-6 text-center">
            <p className="text-sm font-medium text-red-400">Could not connect to backend</p>
            <p className="mt-1 text-xs text-neutral-500">{error}</p>
          </div>
        )}

        {data && !loading && (
          <>
            {leagues.length > 1 && (
              <div className="mb-6">
                <LeagueFilter
                  leagues={leagues}
                  selected={leagueFilter}
                  onChange={setLeagueFilter}
                />
              </div>
            )}

            {filtered.length === 0 ? (
              <NoBetsToday
                matchesAnalyzed={totalAnalyzed}
                date={todayLabel()}
              />
            ) : (
              <>
                <div className="mb-4 flex items-center justify-between text-xs text-neutral-600">
                  <span>
                    {filtered.length}{" "}
                    {filtered.length === 1 ? "match" : "matches"} with value
                    bets{leagueFilter !== "All" && ` in ${leagueFilter}`}
                  </span>
                  <span>{totalAnalyzed} analyzed total</span>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  {filtered.map((fixture) => (
                    <MatchCard key={fixture.id} fixture={fixture} />
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
