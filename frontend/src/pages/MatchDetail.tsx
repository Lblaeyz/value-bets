import { useEffect, useState } from "react";
import { useParams, Link } from "wouter";
import { ValueBadge } from "@/components/ValueBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { api } from "@/lib/api";
import type { MatchDetail as MatchDetailType } from "@/types";

const MARKET_LABELS: Record<string, string> = {
  "1X2_HOME": "Home Win",
  "1X2_DRAW": "Draw",
  "1X2_AWAY": "Away Win",
  BTTS_YES: "BTTS — Yes",
  BTTS_NO: "BTTS — No",
  OU_25_OVER: "Over 2.5",
  OU_25_UNDER: "Under 2.5",
  OU_35_OVER: "Over 3.5",
  OU_35_UNDER: "Under 3.5",
  AH_HOME: "AH Home",
  AH_AWAY: "AH Away",
};

function marketLabel(m: string) {
  return MARKET_LABELS[m] ?? m;
}

function formatDt(iso: string) {
  return new Date(iso).toLocaleString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function MatchDetail() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const [match, setMatch] = useState<MatchDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isNaN(id)) return;
    api.matches
      .detail(id)
      .then(setMatch)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0f0f0f]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
      </div>
    );
  }

  if (error || !match) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#0f0f0f] px-4 text-center">
        <p className="text-sm text-red-400">{error ?? "Match not found"}</p>
        <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-300">
          ← Back
        </Link>
      </div>
    );
  }

  const oddsGrouped = (match.odds ?? []).reduce<
    Record<string, Record<string, number>>
  >((acc, o) => {
    const key = `${o.market} — ${o.selection}`;
    if (!acc[key]) acc[key] = {};
    acc[key][o.bookmaker] = o.odds_decimal;
    return acc;
  }, {});

  const homeInjuries = match.injuries.filter(
    (i) => i.team_id === match.home_team.id
  );
  const awayInjuries = match.injuries.filter(
    (i) => i.team_id === match.away_team.id
  );

  return (
    <div className="min-h-screen bg-[#0f0f0f] px-4 py-8">
      <div className="mx-auto max-w-3xl">
        <Link
          href="/"
          className="mb-6 inline-block text-xs text-neutral-500 hover:text-neutral-300"
        >
          ← Back to dashboard
        </Link>

        <div className="mb-6 rounded-xl border border-white/8 bg-[#1a1a1a] p-5">
          <p className="mb-1 text-xs text-neutral-500">
            {match.league.name} · {match.league.country}
          </p>
          <h1 className="mb-1 text-xl font-bold text-neutral-100">
            {match.home_team.name} vs {match.away_team.name}
          </h1>
          <p className="text-sm text-neutral-500">
            {formatDt(match.kickoff_utc)}
          </p>
          {(match.home_goals !== null || match.away_goals !== null) && (
            <p className="mt-2 text-2xl font-bold tabular-nums text-neutral-100">
              {match.home_goals ?? "–"} : {match.away_goals ?? "–"}
            </p>
          )}
        </div>

        {match.predictions.length > 0 && (
          <section className="mb-6">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">
              Predictions
            </h2>
            <div className="flex flex-col gap-3">
              {match.predictions.map((pred) => (
                <div
                  key={pred.id}
                  className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4"
                >
                  <div className="mb-3 flex items-start justify-between gap-2">
                    <span className="font-semibold text-neutral-100">
                      {marketLabel(pred.market)}
                    </span>
                    <ValueBadge edge={pred.value_edge} />
                  </div>

                  <div className="mb-3 grid grid-cols-3 gap-3 text-center">
                    <div>
                      <p className="text-xs text-neutral-500">Model prob</p>
                      <p className="mt-0.5 text-lg font-bold tabular-nums text-neutral-100">
                        {(pred.model_probability * 100).toFixed(1)}%
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-neutral-500">Bookie prob</p>
                      <p className="mt-0.5 text-lg font-bold tabular-nums text-neutral-100">
                        {(pred.bookmaker_probability * 100).toFixed(1)}%
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-neutral-500">Best odds</p>
                      <p className="mt-0.5 text-lg font-bold tabular-nums text-neutral-100">
                        {pred.recommended_odds.toFixed(2)}
                      </p>
                      {pred.best_bookmaker && (
                        <p className="text-xs text-neutral-600">
                          {pred.best_bookmaker}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="mb-3">
                    <ConfidenceBar value={pred.confidence_score} />
                  </div>

                  {pred.kelly_fraction !== null && (
                    <p className="mb-3 text-xs text-neutral-500">
                      Kelly stake:{" "}
                      <span className="font-medium text-neutral-200">
                        {(pred.kelly_fraction * 100).toFixed(1)}% of bankroll
                      </span>
                    </p>
                  )}

                  {pred.reasoning && (
                    <div className="rounded-md bg-white/5 p-3">
                      <p className="mb-1 text-xs font-medium text-neutral-500">
                        Reasoning
                      </p>
                      <p className="text-xs leading-relaxed text-neutral-400">
                        {pred.reasoning}
                      </p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {match.h2h && (
          <section className="mb-6">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">
              Head to Head
            </h2>
            <div className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4">
              <div className="mb-4 grid grid-cols-4 gap-3 text-center text-xs">
                <div>
                  <p className="text-neutral-500">Home wins</p>
                  <p className="mt-0.5 text-xl font-bold text-neutral-100">
                    {match.h2h.home_wins}
                  </p>
                </div>
                <div>
                  <p className="text-neutral-500">Draws</p>
                  <p className="mt-0.5 text-xl font-bold text-neutral-100">
                    {match.h2h.draws}
                  </p>
                </div>
                <div>
                  <p className="text-neutral-500">Away wins</p>
                  <p className="mt-0.5 text-xl font-bold text-neutral-100">
                    {match.h2h.away_wins}
                  </p>
                </div>
                <div>
                  <p className="text-neutral-500">Avg goals</p>
                  <p className="mt-0.5 text-xl font-bold text-neutral-100">
                    {match.h2h.avg_total_goals.toFixed(1)}
                  </p>
                </div>
              </div>
              {match.h2h.last_5.length > 0 && (
                <div>
                  <p className="mb-2 text-xs text-neutral-500">Last 5</p>
                  <div className="space-y-1.5">
                    {match.h2h.last_5.map((g, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between text-xs"
                      >
                        <span className="text-neutral-600">
                          {new Date(g.date).toLocaleDateString([], {
                            day: "numeric",
                            month: "short",
                            year: "numeric",
                          })}
                        </span>
                        <span className="font-medium tabular-nums text-neutral-300">
                          {g.home_score} – {g.away_score}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        {(homeInjuries.length > 0 || awayInjuries.length > 0) && (
          <section className="mb-6">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">
              Injuries
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                { label: match.home_team.name, list: homeInjuries },
                { label: match.away_team.name, list: awayInjuries },
              ].map(({ label, list }) => (
                <div
                  key={label}
                  className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4"
                >
                  <p className="mb-2 text-xs font-medium text-neutral-400">
                    {label}
                  </p>
                  {list.length === 0 ? (
                    <p className="text-xs text-neutral-600">None reported</p>
                  ) : (
                    <div className="space-y-1">
                      {list.map((inj, i) => (
                        <div
                          key={i}
                          className="flex justify-between text-xs"
                        >
                          <span className="text-neutral-300">
                            {inj.player_name}
                          </span>
                          <span className="text-neutral-500">
                            {inj.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {Object.keys(oddsGrouped).length > 0 && (
          <section className="mb-6">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">
              Odds Comparison
            </h2>
            <div className="flex flex-col gap-3">
              {Object.entries(oddsGrouped).map(([label, books]) => (
                <div
                  key={label}
                  className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4"
                >
                  <p className="mb-2 text-xs font-medium text-neutral-400">
                    {label}
                  </p>
                  <div className="flex flex-wrap gap-x-6 gap-y-1">
                    {Object.entries(books).map(([bk, odds]) => (
                      <div
                        key={bk}
                        className="flex items-baseline gap-1.5 text-xs"
                      >
                        <span className="text-neutral-500">{bk}</span>
                        <span className="font-semibold tabular-nums text-neutral-100">
                          {odds.toFixed(2)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
