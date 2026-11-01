const DEPTH = 4;

export function compactPlaces<Kind extends string>(raw: {
  parties: Array<{
    id: string;
    label: string;
    short: string | null;
    color: string | null;
  }>;
  places: Array<{ name: string; path: string | null }>;
  series: Array<{
    name: string | null;
    isQuestion: boolean;
    rank: number;
    kind: Kind;
    points: Array<{
      name: string;
      path: string | null;
      date: string;
      year: number;
      sides: Array<
        Array<{
          key: string;
          label: string;
          color: string | null;
          party: string | null;
          share: number;
        }>
      >;
    }>;
  }>;
}) {
  const parties = new Map(raw.parties.map((party) => [party.id, party]));
  const keys: Array<{ key: string; label: string; color: string | null }> = [];
  const index = new Map<string, number>();
  const slot = (entry: {
    key: string;
    label: string;
    color: string | null;
    party: string | null;
  }) => {
    const party = entry.party == null ? null : parties.get(entry.party);
    const key = party == null ? entry.key : `party:${party.id}`;
    const known = index.get(key);
    if (known != null) {
      return known;
    }
    index.set(key, keys.length);
    keys.push({
      key,
      label: party == null ? entry.label : (party.short ?? party.label),
      color: party?.color ?? entry.color,
    });
    return keys.length - 1;
  };
  return {
    places: raw.places,
    keys,
    series: raw.series.map((series) => ({
      name: series.name,
      isQuestion: series.isQuestion,
      rank: series.rank,
      kind: series.kind,
      points: series.points.map((point) => ({
        name: point.name,
        path: point.path,
        date: point.date,
        year: point.year,
        sides: point.sides.map((side) =>
          side.slice(0, DEPTH).map((entry) => {
            const k = slot(entry);
            const label = keys[k]?.label;
            return {
              k,
              share: Math.round(entry.share * 1e5) / 1e5,
              who:
                entry.party == null || entry.label === label
                  ? null
                  : entry.label,
            };
          }),
        ),
      })),
    })),
  };
}

export type ComparedPlaces = ReturnType<typeof compactPlaces>;
