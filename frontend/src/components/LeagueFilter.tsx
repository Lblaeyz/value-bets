interface LeagueFilterProps {
  leagues: string[];
  selected: string;
  onChange: (league: string) => void;
}

export function LeagueFilter({ leagues, selected, onChange }: LeagueFilterProps) {
  const all = ["All", ...leagues];

  return (
    <div className="flex flex-wrap gap-2">
      {all.map((league) => {
        const active = league === selected;
        return (
          <button
            key={league}
            onClick={() => onChange(league)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              active
                ? "bg-white text-black"
                : "bg-white/8 text-neutral-400 hover:bg-white/12 hover:text-neutral-200"
            }`}
          >
            {league}
          </button>
        );
      })}
    </div>
  );
}
