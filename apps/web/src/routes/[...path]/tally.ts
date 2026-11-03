export function tallied<D>(places: {
  legend: Array<unknown>;
  rows: Array<{
    path: string | null;
    valid: number;
    leader: number | null;
    declared?: D | null;
  }>;
}) {
  const leads = places.legend.map(() => 0);
  let contested = 0;
  let declared: D | null = null;
  for (const row of places.rows) {
    if (row.leader != null && row.leader < leads.length) {
      leads[row.leader] = (leads[row.leader] ?? 0) + 1;
    }
    if (row.path != null && row.valid > 0) {
      contested++;
    }
    declared ??= row.declared ?? null;
  }
  return { count: places.rows.length, contested, leads, declared };
}

export function led(
  legend: Array<{ key: string }>,
  leads: Array<number>,
  keys: Array<string>,
) {
  let total = 0;
  for (const [at, item] of legend.entries()) {
    if (keys.includes(item.key)) {
      total += leads[at] ?? 0;
    }
  }
  return total;
}
