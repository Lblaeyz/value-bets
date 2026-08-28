import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
  Area,
  AreaChart,
} from "recharts";
import { api } from "@/lib/api";
import type {
  PerformanceSummary,
  PerformanceByLeague,
  PerformanceByMarket,
  ClvTrend,
} from "@/types";

function fmt(n: number | null | undefined, decimals = 1) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

function fmtPct(n: number | null | undefined) {
  if (n == null) return "—";
  const v = (n * 100).toFixed(1);
  return `${n >= 0 ? "+" : ""}${v}%`;
}

function fmtRoi(n: number | null | undefined) {
  if (n == null) return "—";
  const v = (n * 100).toFixed(1);
  return `${n >= 0 ? "+" : ""}${v}%`;
}

const TOOLTIP_STYLE = {
  contentStyle: {
    background: "#1a1a1a",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: "8px",
    fontSize: "12px",
    color: "#d4d4d4",
  },
  labelStyle: { color: "#737373", marginBottom: 2 },
};

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-neutral-500">
      {children}
    </h2>
  );
}

function StatCard({
  label,
  value,
  sub,
  color,
  dimmed,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  dimmed?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border bg-[#1a1a1a] p-5 transition-opacity ${
        dimmed ? "border-white/5 opacity-40" : "border-white/8"
      }`}
    >
      <p className="mb-1.5 text-xs text-neutral-500">{label}</p>
      <p
        className={`text-2xl font-bold tabular-nums ${
          dimmed ? "text-neutral-500" : (color ?? "text-neutral-100")
        }`}
      >
        {value}
      </p>
      {sub && (
        <p className="mt-1 text-xs text-neutral-600">{sub}</p>
      )}
    </div>
  );
}

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex h-44 items-center justify-center rounded-xl border border-white/8 bg-[#1a1a1a]">
      <p className="text-xs text-neutral-600">{message}</p>
    </div>
  );
}

export function Performance() {
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [leagues, setLeagues] = useState<PerformanceByLeague | null>(null);
  const [markets, setMarkets] = useState<PerformanceByMarket | null>(null);
  const [clv, setClv] = useState<ClvTrend | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.performance.summary(),
      api.performance.byLeague(),
      api.performance.byMarket(),
      api.performance.clvTrend(90),
    ])
      .then(([s, l, m, c]) => {
        setSummary(s);
        setLeagues(l);
        setMarkets(m);
        setClv(c);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const dimmed = summary?.insufficient_data ?? false;
  const settledBets = summary ? summary.wins + summary.losses : 0;
  const winRate = settledBets > 0 && summary ? summary.wins / settledBets : 0;

  const clvChartData = clv?.data ?? [];

  const plChartData = (() => {
    let cumulative = 0;
    return clvChartData.map((row) => {
      cumulative += row.avg_clv * row.count;
      return {
        date: row.date,
        cumulative_pl: parseFloat(cumulative.toFixed(3)),
      };
    });
  })();

  return (
    <div className="min-h-screen bg-[#0f0f0f] px-4 py-8">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8">
          <h1 className="text-2xl font-bold tracking-tight text-neutral-100">
            Performance
          </h1>
          <p className="mt-0.5 text-sm text-neutral-500">
            Every prediction tracked from day one
          </p>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-32">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/60" />
          </div>
        )}

        {error && !loading && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/8 px-5 py-4 text-sm text-red-400">
            Could not load performance data — {error}
          </div>
        )}

        {!loading && !error && summary && (
          <div className="flex flex-col gap-10">

            {/* ── SECTION 1: Summary Stats ── */}
            <section>
              <SectionTitle>Summary</SectionTitle>

              {summary.total_predictions === 0 && (
                <div className="mb-4 rounded-lg border border-blue-500/20 bg-blue-500/8 px-4 py-3 text-sm text-blue-300">
                  No performance data yet — results will appear here after
                  predictions have been settled.
                </div>
              )}

              {dimmed && (
                <div className="mb-4 rounded-lg border border-yellow-500/20 bg-yellow-500/8 px-4 py-3 text-sm text-yellow-400">
                  Insufficient data —{" "}
                  <span className="font-semibold">{summary.total_predictions}</span>{" "}
                  {summary.total_predictions === 1 ? "prediction" : "predictions"} recorded.
                  Minimum 50 needed for meaningful statistics.
                </div>
              )}

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard
                  label="Total Predictions"
                  value={String(summary.total_predictions)}
                  sub={`${summary.wins}W · ${summary.losses}L · ${summary.voids}V`}
                  dimmed={dimmed}
                />
                <StatCard
                  label="ROI"
                  value={fmtRoi(summary.roi)}
                  sub={`yield ${fmtRoi(summary.yield_pct)}`}
                  color={summary.roi >= 0 ? "text-green-400" : "text-red-400"}
                  dimmed={dimmed}
                />
                <StatCard
                  label="Win Rate"
                  value={`${(winRate * 100).toFixed(1)}%`}
                  sub={`${settledBets} settled bets`}
                  dimmed={dimmed}
                />
                <StatCard
                  label="Average CLV"
                  value={
                    summary.avg_clv !== null
                      ? summary.avg_clv.toFixed(3)
                      : "—"
                  }
                  sub="closing line value"
                  color={
                    summary.avg_clv !== null
                      ? summary.avg_clv > 0
                        ? "text-green-400"
                        : "text-red-400"
                      : "text-neutral-500"
                  }
                  dimmed={dimmed}
                />
              </div>
            </section>

            {/* ── SECTION 2: P&L Chart ── */}
            <section>
              <SectionTitle>Cumulative P&L</SectionTitle>

              {plChartData.length === 0 ? (
                <EmptyChart message="No settled results yet — P&L will appear here once bets are graded." />
              ) : (
                <div className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4">
                  <ResponsiveContainer width="100%" height={200}>
                    <AreaChart
                      data={plChartData}
                      margin={{ top: 8, right: 4, bottom: 0, left: -8 }}
                    >
                      <defs>
                        <linearGradient id="plGreen" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#22c55e" stopOpacity={0.18} />
                          <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="plRed" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ef4444" stopOpacity={0.18} />
                          <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(255,255,255,0.05)"
                      />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: "#525252" }}
                        tickFormatter={(v: string) => v.slice(5)}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: "#525252" }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v: number) => v.toFixed(1)}
                      />
                      <Tooltip
                        {...TOOLTIP_STYLE}
                        formatter={(v: number) => [v.toFixed(3), "Cumulative P&L"]}
                      />
                      <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
                      <Area
                        type="monotone"
                        dataKey="cumulative_pl"
                        stroke={
                          plChartData.length > 0 &&
                          plChartData[plChartData.length - 1].cumulative_pl >= 0
                            ? "#22c55e"
                            : "#ef4444"
                        }
                        strokeWidth={2}
                        fill={
                          plChartData.length > 0 &&
                          plChartData[plChartData.length - 1].cumulative_pl >= 0
                            ? "url(#plGreen)"
                            : "url(#plRed)"
                        }
                        dot={false}
                        activeDot={{ r: 4 }}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                  <p className="mt-2 text-right text-xs text-neutral-600">
                    Derived from CLV · actual P&L available after results are graded
                  </p>
                </div>
              )}
            </section>

            {/* ── SECTION 3: CLV Trend ── */}
            <section>
              <SectionTitle>CLV Trend — last 90 days</SectionTitle>

              {clvChartData.length === 0 ? (
                <EmptyChart message="No CLV data yet — requires graded predictions with closing odds." />
              ) : (
                <div className="rounded-xl border border-white/8 bg-[#1a1a1a] p-4">
                  <div className="mb-3 flex items-baseline justify-between">
                    <p className="text-xs text-neutral-500">
                      Positive CLV means the model found edge before the market closed
                    </p>
                    {clv?.overall_avg_clv !== null && clv?.overall_avg_clv !== undefined && (
                      <span
                        className={`text-sm font-bold tabular-nums ${
                          clv.overall_avg_clv > 0 ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        avg {clv.overall_avg_clv.toFixed(3)}
                      </span>
                    )}
                  </div>
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart
                      data={clvChartData}
                      margin={{ top: 4, right: 4, bottom: 0, left: -8 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(255,255,255,0.05)"
                      />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: "#525252" }}
                        tickFormatter={(v: string) => v.slice(5)}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: "#525252" }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v: number) => v.toFixed(2)}
                      />
                      <Tooltip
                        {...TOOLTIP_STYLE}
                        formatter={(v: number, _: string, props: { payload?: { count?: number } }) => [
                          `${v.toFixed(3)} (n=${props?.payload?.count ?? "?"})`,
                          "Avg CLV",
                        ]}
                      />
                      <ReferenceLine
                        y={0}
                        stroke="rgba(255,255,255,0.25)"
                        strokeDasharray="4 4"
                        label={{
                          value: "0",
                          position: "right",
                          fontSize: 10,
                          fill: "#525252",
                        }}
                      />
                      <Line
                        type="monotone"
                        dataKey="avg_clv"
                        stroke="#3b82f6"
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4, fill: "#3b82f6" }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                  {clv?.interpretation && (
                    <p className="mt-2 text-xs text-neutral-600">{clv.interpretation}</p>
                  )}
                </div>
              )}
            </section>

            {/* ── SECTION 4: By League ── */}
            {leagues && leagues.data.length > 0 && (
              <section>
                <SectionTitle>By League</SectionTitle>
                <div className="overflow-hidden rounded-xl border border-white/8 bg-[#1a1a1a]">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-white/8 text-left text-neutral-500">
                        <th className="px-4 py-3 font-medium">League</th>
                        <th className="px-4 py-3 text-right font-medium">Bets</th>
                        <th className="px-4 py-3 text-right font-medium">Win Rate</th>
                        <th className="px-4 py-3 text-right font-medium">ROI</th>
                        <th className="px-4 py-3 text-right font-medium">Avg CLV</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...leagues.data]
                        .sort((a, b) => b.roi - a.roi)
                        .map((row, i) => {
                          const highlight = row.roi > 0.1;
                          const rowSettled = row.wins + row.losses;
                          return (
                            <tr
                              key={row.id}
                              className={`border-b border-white/5 last:border-0 transition-colors ${
                                highlight
                                  ? "bg-green-500/5 hover:bg-green-500/8"
                                  : "hover:bg-white/3"
                              } ${i % 2 === 0 && !highlight ? "bg-white/1" : ""}`}
                            >
                              <td className="px-4 py-2.5">
                                <span
                                  className={`font-medium ${
                                    highlight ? "text-green-300" : "text-neutral-200"
                                  }`}
                                >
                                  {row.name}
                                  {row.country ? ` · ${row.country}` : ""}
                                </span>
                              </td>
                              <td className="px-4 py-2.5 text-right tabular-nums text-neutral-400">
                                {row.total_predictions}
                              </td>
                              <td className="px-4 py-2.5 text-right tabular-nums text-neutral-300">
                                {rowSettled > 0
                                  ? `${((row.wins / rowSettled) * 100).toFixed(1)}%`
                                  : "—"}
                              </td>
                              <td
                                className={`px-4 py-2.5 text-right tabular-nums font-semibold ${
                                  row.roi >= 0 ? "text-green-400" : "text-red-400"
                                }`}
                              >
                                {fmtRoi(row.roi)}
                              </td>
                              <td
                                className={`px-4 py-2.5 text-right tabular-nums ${
                                  row.avg_clv !== null && row.avg_clv > 0
                                    ? "text-green-400"
                                    : row.avg_clv !== null && row.avg_clv < 0
                                    ? "text-red-400"
                                    : "text-neutral-500"
                                }`}
                              >
                                {row.avg_clv !== null ? row.avg_clv.toFixed(3) : "—"}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* ── SECTION 5: By Market ── */}
            {markets && markets.data.length > 0 && (
              <section>
                <SectionTitle>By Market</SectionTitle>
                <div className="overflow-hidden rounded-xl border border-white/8 bg-[#1a1a1a]">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-white/8 text-left text-neutral-500">
                        <th className="px-4 py-3 font-medium">Market</th>
                        <th className="px-4 py-3 text-right font-medium">Bets</th>
                        <th className="px-4 py-3 text-right font-medium">Win Rate</th>
                        <th className="px-4 py-3 text-right font-medium">ROI</th>
                        <th className="px-4 py-3 text-right font-medium">Avg CLV</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...markets.data]
                        .sort((a, b) => b.roi - a.roi)
                        .map((row, i) => {
                          const highlight = row.roi > 0.1;
                          const rowSettled = row.wins + row.losses;
                          return (
                            <tr
                              key={row.market}
                              className={`border-b border-white/5 last:border-0 transition-colors ${
                                highlight
                                  ? "bg-green-500/5 hover:bg-green-500/8"
                                  : "hover:bg-white/3"
                              } ${i % 2 === 0 && !highlight ? "bg-white/1" : ""}`}
                            >
                              <td className="px-4 py-2.5">
                                <span
                                  className={`font-medium ${
                                    highlight ? "text-green-300" : "text-neutral-200"
                                  }`}
                                >
                                  {row.market}
                                </span>
                              </td>
                              <td className="px-4 py-2.5 text-right tabular-nums text-neutral-400">
                                {row.total_predictions}
                              </td>
                              <td className="px-4 py-2.5 text-right tabular-nums text-neutral-300">
                                {rowSettled > 0
                                  ? `${((row.wins / rowSettled) * 100).toFixed(1)}%`
                                  : "—"}
                              </td>
                              <td
                                className={`px-4 py-2.5 text-right tabular-nums font-semibold ${
                                  row.roi >= 0 ? "text-green-400" : "text-red-400"
                                }`}
                              >
                                {fmtRoi(row.roi)}
                              </td>
                              <td
                                className={`px-4 py-2.5 text-right tabular-nums ${
                                  row.avg_clv !== null && row.avg_clv > 0
                                    ? "text-green-400"
                                    : row.avg_clv !== null && row.avg_clv < 0
                                    ? "text-red-400"
                                    : "text-neutral-500"
                                }`}
                              >
                                {row.avg_clv !== null ? row.avg_clv.toFixed(3) : "—"}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* ── Footer ── */}
            <footer className="border-t border-white/8 pt-6 text-center">
              <p className="text-xs leading-relaxed text-neutral-600">
                All predictions tracked from day one. No cherry-picking.
                <br />
                Winners and losers both shown.
              </p>
            </footer>
          </div>
        )}
      </div>
    </div>
  );
}
