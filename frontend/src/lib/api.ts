import type {
  TodayResponse,
  MatchDetail,
  PaginatedPredictions,
  PerformanceSummary,
  PerformanceByLeague,
  PerformanceByMarket,
  ClvTrend,
  BudgetStatus,
} from "@/types";

const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString());
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  matches: {
    today: () => get<TodayResponse>("/api/matches/today"),
    byDate: (date: string) => get<TodayResponse>(`/api/matches/date/${date}`),
    detail: (id: number) => get<MatchDetail>(`/api/matches/${id}`),
  },
  predictions: {
    list: (params?: Record<string, string | number | undefined>) =>
      get<PaginatedPredictions>("/api/predictions", params),
    recommended: () => get<PaginatedPredictions>("/api/predictions/recommended"),
  },
  performance: {
    summary: () => get<PerformanceSummary>("/api/performance/summary"),
    byLeague: () => get<PerformanceByLeague>("/api/performance/by-league"),
    byMarket: () => get<PerformanceByMarket>("/api/performance/by-market"),
    clvTrend: (days?: number) =>
      get<ClvTrend>("/api/performance/clv", days ? { days } : undefined),
  },
  admin: {
    budget: () => get<BudgetStatus>("/api/admin/budget"),
    runPipeline: () =>
      post<{ success: boolean; summary: Record<string, unknown> }>("/api/admin/run-pipeline"),
  },
};
