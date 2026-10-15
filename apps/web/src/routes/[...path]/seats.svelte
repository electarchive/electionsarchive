<script lang="ts">
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    parties,
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      seats: number;
    }>;
  } = $props();

  let hovered = $state<string | null>(null);

  const total = $derived.by(() => {
    let sum = 0;
    for (const party of parties) {
      sum += party.seats;
    }
    return sum;
  });
  const majority = $derived(Math.floor(total / 2) + 1);
  const rows = $derived(
    Math.max(2, Math.min(14, Math.round(Math.sqrt(total * 0.27)))),
  );
  const inner = $derived(rows <= 3 ? 0.5 : 0.36);
  const seats = $derived.by(() => {
    const radii: Array<number> = [];
    let radiusSum = 0;
    for (let row = 0; row < rows; row++) {
      const radius = inner + ((1 - inner) * row) / (rows - 1);
      radii.push(radius);
      radiusSum += radius;
    }
    const raw = radii.map((radius) => (total * radius) / radiusSum);
    const counts = raw.map((value) => Math.floor(value));
    let assigned = 0;
    for (const value of counts) {
      assigned += value;
    }
    for (const { row } of raw
      .map((value, row) => ({ rest: value - Math.floor(value), row }))
      .toSorted((a, b) => b.rest - a.rest)) {
      if (assigned >= total) {
        break;
      }
      counts[row] = (counts[row] ?? 0) + 1;
      assigned += 1;
    }
    const positions: Array<{ angle: number; radius: number }> = [];
    for (const [row, radius] of radii.entries()) {
      const seatsInRow = counts[row] ?? 0;
      for (let seat = 0; seat < seatsInRow; seat++) {
        positions.push({
          angle:
            seatsInRow === 1
              ? Math.PI / 2
              : Math.PI - (seat * Math.PI) / (seatsInRow - 1),
          radius,
        });
      }
    }
    positions.sort((a, b) => b.angle - a.angle || a.radius - b.radius);
    const owners: Array<string> = [];
    for (const party of parties) {
      for (let seat = 0; seat < party.seats; seat++) {
        owners.push(party.key);
      }
    }
    return positions.map((position, index) => ({
      x: 100 + position.radius * 96 * Math.cos(position.angle),
      y: 100 - position.radius * 96 * Math.sin(position.angle),
      party: owners[index] ?? "",
      index,
    }));
  });
  const colors = $derived(
    new Map(
      parties.map((party) => [party.key, swatch(party.color, party.key)]),
    ),
  );
  const dot = $derived(((1 - inner) / (rows - 1)) * 96 * 0.42);
  const majorityAngle = $derived.by(() => {
    const before = seats[majority - 2];
    const after = seats[majority - 1];
    if (before == null || after == null) {
      return Math.PI / 2;
    }
    return (
      (Math.atan2(100 - before.y, before.x - 100) +
        Math.atan2(100 - after.y, after.x - 100)) /
      2
    );
  });
</script>

<section
  class="col-span-12 grid content-start gap-3 bg-card p-3.5 lg:col-span-4"
  aria-labelledby="seats-title"
>
  <h2
    id="seats-title"
    class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
  >
    {m.seats_title()}
  </h2>
  <svg
    class="mx-auto block overflow-visible inline-full max-inline-104"
    viewBox="0 -8 200 116"
    role="img"
    aria-label="{count(total)} {m.seats_title()}"
  >
    {#each seats as seat (seat.index)}
      <circle
        class={cn(
          "transition-opacity duration-150 motion-reduce:transition-none",
          { "opacity-15": hovered != null && hovered !== seat.party },
        )}
        cx={seat.x}
        cy={seat.y}
        r={dot}
        fill={colors.get(seat.party)}
        role="presentation"
        onpointerenter={() => (hovered = seat.party)}
        onpointerleave={() => (hovered = null)}
      />
    {/each}
    <line
      class="stroke-foreground"
      stroke-width="1.4"
      stroke-linecap="round"
      x1={100 + (1 + dot / 96 + 0.03) * 96 * Math.cos(majorityAngle)}
      y1={100 - (1 + dot / 96 + 0.03) * 96 * Math.sin(majorityAngle)}
      x2={100 + (1 + dot / 96 + 0.11) * 96 * Math.cos(majorityAngle)}
      y2={100 - (1 + dot / 96 + 0.11) * 96 * Math.sin(majorityAngle)}
    />
    <text
      class="fill-foreground font-serif text-[26px] tabular-nums"
      x="100"
      y="97"
      text-anchor="middle">{count(total)}</text
    >
  </svg>
  <p class="text-center text-[0.8125rem] text-muted-foreground">
    {m.seats_majority({ count: count(majority) })}
  </p>
  <ul class="grid gap-px text-sm/tight">
    {#each parties as party (party.key)}
      <li
        class={cn("flex items-center gap-2 rounded-xs px-1.5 py-1", {
          "bg-secondary": hovered === party.key,
        })}
        style:--party={colors.get(party.key)}
        onpointerenter={() => (hovered = party.key)}
        onpointerleave={() => (hovered = null)}
      >
        <span class="shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5"
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0"
          >{party.short ?? party.label}</span
        >
        <span class="font-mono text-[0.8125rem] font-semibold tabular-nums"
          >{count(party.seats)}</span
        >
        <span
          class="text-end font-mono text-[0.8125rem] text-muted-foreground tabular-nums inline-14"
          >{percent(party.seats / total)}</span
        >
      </li>
    {/each}
  </ul>
</section>
