-- ============================================================
-- Enable Supabase RLS for the public schema
-- ============================================================
--
-- The frontend talks to FastAPI, not Supabase directly. FastAPI must use
-- the Supabase service-role key, which bypasses RLS server-side. Anonymous
-- and authenticated PostgREST clients receive no direct table access.
--
-- Run this once in the Supabase SQL Editor for the project database.

BEGIN;

ALTER TABLE public.leagues              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.teams                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fixtures             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.odds                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.line_movements       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.injuries              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lineups               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.h2h_cache             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.results               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.performance_summary   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_call_budget       ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    public.leagues,
    public.teams,
    public.fixtures,
    public.odds,
    public.line_movements,
    public.injuries,
    public.lineups,
    public.h2h_cache,
    public.predictions,
    public.results,
    public.performance_summary,
    public.api_call_budget
FROM anon, authenticated;

COMMIT;