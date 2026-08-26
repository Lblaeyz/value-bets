import { Switch, Route, Router as WouterRouter, Link, useLocation } from "wouter";
import { Dashboard } from "@/pages/Dashboard";
import { Performance } from "@/pages/Performance";
import { MatchDetail } from "@/pages/MatchDetail";

function NavBar() {
  const [loc] = useLocation();

  function navLink(href: string, label: string) {
    const active = href === "/" ? loc === "/" : loc.startsWith(href);
    return (
      <Link
        href={href}
        className={`text-sm font-medium transition-colors ${
          active ? "text-neutral-100" : "text-neutral-500 hover:text-neutral-300"
        }`}
      >
        {label}
      </Link>
    );
  }

  return (
    <nav className="sticky top-0 z-10 border-b border-white/8 bg-[#0f0f0f]/90 backdrop-blur">
      <div className="mx-auto flex h-12 max-w-5xl items-center gap-6 px-4">
        <Link href="/" className="text-sm font-semibold text-neutral-100">
          ⚽ ValueBets
        </Link>
        <div className="flex gap-4">
          {navLink("/", "Dashboard")}
          {navLink("/performance", "Performance")}
        </div>
      </div>
    </nav>
  );
}

function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <p className="text-4xl">404</p>
      <p className="text-neutral-500">Page not found</p>
      <Link href="/" className="text-sm text-blue-400 hover:text-blue-300">
        Go to dashboard
      </Link>
    </div>
  );
}

function Router() {
  return (
    <>
      <NavBar />
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/performance" component={Performance} />
        <Route path="/matches/:id" component={MatchDetail} />
        <Route component={NotFound} />
      </Switch>
    </>
  );
}

export default function App() {
  return (
    <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <Router />
    </WouterRouter>
  );
}
