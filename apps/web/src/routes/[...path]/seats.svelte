<script lang="ts">
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  const {
    parties,
    place,
    link,
    size,
  }: {
    parties: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      seats: number;
      here: number;
    }>;
    place: string;
    link: { name: string; path: string | null } | null;
    size: number | null;
  } = $props();

  let hovered = $state<string | null>(null);

  const ordered = $derived(
    parties.toSorted(
      (a, b) =>
        (a.lean ?? Infinity) - (b.lean ?? Infinity) || b.seats - a.seats,
    ),
  );
  const totals = $derived.by(() => {
    let [all, local] = [0, 0];
    for (const party of parties) {
      all += party.seats;
      local += party.here;
    }
    return { all, local };
  });
  const majority = $derived(Math.floor(totals.all / 2) + 1);
  const layout = $derived.by(() => {
    let best = { gap: 0, radii: [1], counts: [totals.all] };
    for (let rows = 1; rows <= 24; rows++) {
      const radii = Array.from({ length: rows }, (_, row) =>
        rows === 1 ? 1 : 0.4 + (0.6 * row) / (rows - 1),
      );
      let sum = 0;
      for (const radius of radii) {
        sum += radius;
      }
      const raw = radii.map((radius) => (totals.all * radius) / sum);
      const counts = raw.map((value) => Math.floor(value));
      let left = totals.all;
      for (const value of counts) {
        left -= value;
      }
      for (const { row } of raw
        .map((value, row) => ({ rest: value - Math.floor(value), row }))
        .toSorted((a, b) => b.rest - a.rest)
        .slice(0, left)) {
        counts[row] = (counts[row] ?? 0) + 1;
      }
      let gap = rows === 1 ? Infinity : 0.6 / (rows - 1);
      for (const [row, radius] of radii.entries()) {
        const seats = counts[row] ?? 0;
        gap = Math.min(gap, seats > 1 ? (Math.PI * radius) / (seats - 1) : gap);
      }
      if (!counts.includes(0) && gap > best.gap) {
        best = { gap, radii, counts };
      }
    }
    return best;
  });
  const dot = $derived(Math.min(0.42 * layout.gap, 0.5) * 96);
  const scale = $derived(Math.min(1, (0.06 * 96) / dot));
  const seats = $derived.by(() => {
    const positions: Array<{ angle: number; radius: number }> = [];
    for (const [row, radius] of layout.radii.entries()) {
      const count = layout.counts[row] ?? 0;
      for (let seat = 0; seat < count; seat++) {
        positions.push({
          angle:
            count === 1
              ? Math.PI / 2
              : Math.PI - (seat * Math.PI) / (count - 1),
          radius,
        });
      }
    }
    positions.sort((a, b) => b.angle - a.angle || a.radius - b.radius);
    const placed = positions.map((position, index) => ({
      x: 100 + position.radius * 96 * Math.cos(position.angle),
      y: 100 - position.radius * 96 * Math.sin(position.angle),
      angle: position.angle,
      party: "",
      local: false,
      index,
    }));
    let start = 0;
    for (const party of ordered) {
      const block = placed.slice(start, start + party.seats);
      start += party.seats;
      const angle = ((block[0]?.angle ?? 0) + (block.at(-1)?.angle ?? 0)) / 2;
      const [x, y] = [100 + 67 * Math.cos(angle), 100 - 67 * Math.sin(angle)];
      for (const seat of block) {
        seat.party = party.key;
      }
      for (const seat of block
        .toSorted(
          (a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y),
        )
        .slice(0, link == null ? 0 : party.here)) {
        seat.local = true;
      }
    }
    return placed;
  });
  const colors = $derived(
    new Map(
      parties.map((party) => [party.key, swatch(party.color, party.key)]),
    ),
  );
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
  const whole = $derived(link == null && (size == null || size === totals.all));
  const reach = $derived(96 + dot + 2);
  const top = $derived(whole ? reach + 12 : reach);
  const center = $derived(
    link == null
      ? { value: totals.all, of: whole ? null : size }
      : { value: totals.local, of: totals.all },
  );
  const font = $derived.by(() => {
    const hole = 38.4 - dot - 2;
    const width = 0.28 * count(center.value).length;
    return Math.min(
      30,
      center.of == null
        ? (hole - 2) / Math.hypot(width, 0.72)
        : hole / Math.hypot(width, 1.18),
    );
  });
  const caption = $derived.by(() => {
    if (link != null) {
      return m.seats_focus({
        count: count(totals.local),
        total: count(totals.all),
        place,
      });
    }
    return whole
      ? m.seats_majority({ count: count(majority) })
      : m.seats_part({ count: count(totals.all), total: count(size ?? 0) });
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
    class="mx-auto block overflow-visible inline-full"
    style:max-inline-size="{26 * scale}rem"
    viewBox="{100 - reach} {100 - top} {2 * reach} {top + dot + 2}"
    role="img"
    aria-label={whole ? `${count(totals.all)} ${m.seats_title()}` : caption}
  >
    {#if link != null}
      <g class="fill-foreground opacity-16" aria-hidden="true">
        {#each seats.filter((seat) => seat.local) as seat (seat.index)}
          <circle cx={seat.x} cy={seat.y} r={dot + 0.5 * layout.gap * 96} />
        {/each}
      </g>
    {/if}
    {#each seats as seat (seat.index)}
      {const lit = $derived(
        hovered == null ? link == null || seat.local : hovered === seat.party,
      )}
      <circle
        class="transition-[fill] duration-150 motion-reduce:transition-none"
        cx={seat.x}
        cy={seat.y}
        r={dot}
        style:fill={lit
          ? colors.get(seat.party)
          : `color-mix(in oklab, ${colors.get(seat.party)} 24%, var(--card))`}
        role="presentation"
        onpointerenter={() => (hovered = seat.party)}
        onpointerleave={() => (hovered = null)}
      />
    {/each}
    {#if whole}
      <line
        class="stroke-foreground"
        stroke-width="1.4"
        stroke-linecap="round"
        x1={100 + (96 + dot + 3) * Math.cos(majorityAngle)}
        y1={100 - (96 + dot + 3) * Math.sin(majorityAngle)}
        x2={100 + (96 + dot + 11) * Math.cos(majorityAngle)}
        y2={100 - (96 + dot + 11) * Math.sin(majorityAngle)}
      />
    {/if}
    <text
      class="fill-foreground font-serif tabular-nums"
      style:font-size="{font}px"
      x="100"
      y={center.of == null ? 98 : 99 - 0.38 * font}
      text-anchor="middle">{count(center.value)}</text
    >
    {#if center.of != null}
      <text
        class="fill-muted-foreground tabular-nums"
        style:font-size="{0.34 * font}px"
        x="100"
        y="99"
        text-anchor="middle">{m.seats_of({ count: count(center.of) })}</text
      >
    {/if}
  </svg>
  <p class="text-center text-[0.8125rem] text-pretty text-muted-foreground">
    {caption}
    {#if link?.path != null}
      <Preview
        class="font-semibold whitespace-nowrap text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={link.path}>{m.seats_whole({ place: link.name })}</Preview
      >
    {/if}
  </p>
  <ul class="grid gap-px text-sm/tight">
    {#each ordered as party (party.key)}
      <li
        class={cn(
          "flex items-center gap-2 rounded-xs px-1.5 py-1",
          { "bg-secondary": hovered === party.key },
          { "text-muted-foreground": link != null && party.here === 0 },
        )}
        style:--party={colors.get(party.key)}
        onpointerenter={() => (hovered = party.key)}
        onpointerleave={() => (hovered = null)}
      >
        <span
          class={cn("shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5", {
            "opacity-35": link != null && party.here === 0,
          })}
        ></span>
        <span class="flex-1 truncate font-semibold min-inline-0"
          >{party.short ?? party.label}</span
        >
        {#if link == null}
          <span class="font-mono text-[0.8125rem] font-semibold tabular-nums"
            >{count(party.seats)}</span
          >
          <span
            class="text-end font-mono text-[0.8125rem] text-muted-foreground tabular-nums inline-14"
            >{percent(party.seats / totals.all)}</span
          >
        {:else}
          <span class="font-mono text-[0.8125rem] font-semibold tabular-nums"
            >{count(party.here)}</span
          >
          <span
            class="text-end font-mono text-[0.8125rem] text-muted-foreground tabular-nums inline-16"
            >{m.seats_of({ count: count(party.seats) })}</span
          >
        {/if}
      </li>
    {/each}
  </ul>
</section>
