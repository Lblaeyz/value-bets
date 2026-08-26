import { useState } from "react";
import { Link } from "wouter";
import { ValueBadge } from "@/components/ValueBadge";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import type { TodayFixture, Prediction } from "@/types";

const COUNTRY_FLAGS: Record<string, string> = {
  England: "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
  Germany: "🇩🇪",
  Spain: "🇪🇸",
  France: "🇫🇷",
  Italy: "🇮🇹",
  Netherlands: "🇳🇱",
  Portugal: "🇵🇹",
  Belgium: "🇧🇪",
  Scotland: "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
  Turkey: "🇹🇷",
  Greece: "🇬🇷",
  Brazil: "🇧🇷",
  Argentina: "🇦🇷",
  USA: "🇺🇸",
  Mexico: "🇲🇽",
  World: "🌍",
};

function flag(country: string) {
  return COUNTRY_FLAGS[country] ?? "🏳";
}

function formatKickoff(iso: string) {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

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

function marketLabel(market: string) {
  return MARKET_LABELS[market] ?? market;
}

function PredictionRow({ pred }: { pred: Prediction }) {
  const [expanded, setExpanded] = useState(false);
  const kelly =
    pred.kelly_fraction !== null
      ? (pred.kelly_fraction * 100).toFixed(1)
      : null;

  return (
    <div className="border-t border-white/6 pt-3 first:border-0 first:pt-0">
      <div className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm font-semibold text-neutral-100">
            {marketLabel(pred.market)}
          </span>
          <ValueBadge edge={pred.value_edge} />
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <div className="flex justify-between text-neutral-500">
            <span>Model prob</span>
            <span className="tabular-nums text-neutral-200">
              {(pred.model_probability * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex justify-between text-neutral-500">
            <span>Bookie prob</span>
            <span className="tabular-nums text-neutral-200">
              {(pred.bookmaker_probability * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex justify-between text-neutral-500">
            <span>Best odds</span>
            <span className="tabular-nums font-medium text-neutral-100">
              {pred.recommended_odds.toFixed(2)}
              {pred.best_bookmaker && (
                <span className="ml-1 font-normal text-neutral-500">
                  @ {pred.best_bookmaker}
                </span>
              )}
            </span>
          </div>
          {kelly !== null && (
            <div className="flex justify-between text-neutral-500">
              <span>Kelly stake</span>
              <span className="tabular-nums text-neutral-200">{kelly}%</span>
            </div>
          )}
        </div>

        <div>
          <p className="mb-1 text-xs text-neutral-500">Confidence</p>
          <ConfidenceBar value={pred.confidence_score} />
        </div>

        {pred.reasoning && (
          <div>
            <button
              onClick={() => setExpanded((e) => !e)}
              className="text-xs text-blue-400 hover:text-blue-300"
            >
              {expanded ? "Hide reasoning ▲" : "Show reasoning ▼"}
            </button>
            {expanded && (
              <p className="mt-1 rounded-md bg-white/5 p-2 text-xs leading-relaxed text-neutral-400">
                {pred.reasoning}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface MatchCardProps {
  fixture: TodayFixture;
}

export function MatchCard({ fixture }: MatchCardProps) {
  return (
    <div className="flex flex-col rounded-xl border border-white/8 bg-[#1a1a1a] transition-colors hover:border-white/12">
      <Link href={`/matches/${fixture.id}`} className="block p-4 pb-3">
        <div className="mb-1 flex items-center gap-1.5 text-xs text-neutral-500">
          <span>{flag(fixture.league.country)}</span>
          <span>{fixture.league.name}</span>
          <span className="ml-auto tabular-nums">
            {formatKickoff(fixture.kickoff_utc)}
          </span>
        </div>
        <h3 className="text-base font-semibold text-neutral-100">
          {fixture.home_team.name}{" "}
          <span className="text-neutral-500">vs</span>{" "}
          {fixture.away_team.name}
        </h3>
      </Link>

      <div className="flex flex-col gap-3 border-t border-white/6 px-4 py-3">
        {fixture.predictions.length > 0 ? (
          fixture.predictions.map((p) => (
            <PredictionRow key={p.id} pred={p} />
          ))
        ) : (
          <p className="text-xs text-neutral-600">No predictions</p>
        )}
      </div>

      <div className="px-4 pb-3">
        <Link
          href={`/matches/${fixture.id}`}
          className="block rounded-md bg-white/5 py-1.5 text-center text-xs font-medium text-neutral-400 transition-colors hover:bg-white/8 hover:text-neutral-200"
        >
          Full match detail →
        </Link>
      </div>
    </div>
  );
}
