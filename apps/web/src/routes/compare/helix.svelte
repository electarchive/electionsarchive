<script lang="ts">
  import type { Snippet } from "svelte";

  type Race = {
    key: string;
    x: number;
    share: number;
    isWin: boolean;
    isPrimary: boolean;
    votes: number | null;
    color: string;
  };
  type Lane = {
    color: string;
    races: Array<Race>;
    terms: Array<{ from: number; to: number; color: string }>;
  };

  let {
    lanes,
    links,
    active = $bindable(null),
    readout,
  }: {
    lanes: [Lane, Lane];
    links: Array<{ x: number; shares: [number, number]; color: string }>;
    active?: string | null;
    readout: Snippet<[string]>;
  } = $props();

  const PAD = { left: 34, right: 12, top: 8 };
  const BAND = 22;
  const AXIS = 18;

  let width = $state(0);

  const lane = $derived(Math.round(Math.min(150, Math.max(96, width * 0.11))));
  const middle = $derived(PAD.top + lane + BAND / 2);
  const all = $derived(lanes.flatMap((entry) => entry.races));
  const first = $derived(
    Math.min(
      ...all.map((race) => race.x),
      ...lanes.flatMap((entry) => entry.terms.map((term) => term.from)),
    ) - 0.5,
  );
  const last = $derived(
    Math.max(
      ...all.map((race) => race.x),
      ...lanes.flatMap((entry) => entry.terms.map((term) => term.to)),
    ) + 0.5,
  );
  const most = $derived(Math.max(1, ...all.map((race) => race.votes ?? 0)));
  const sx = (x: number) =>
    PAD.left +
    ((x - first) / Math.max(1, last - first)) * (width - PAD.left - PAD.right);
  const sy = (side: number, share: number) =>
    side === 0
      ? middle - BAND / 2 - share * lane
      : middle + BAND / 2 + share * lane;
  const size = (race: Race) =>
    race.votes == null ? 3 : 2.5 + 5.5 * Math.sqrt(race.votes / most);
  const years = $derived.by(() => {
    const span = last - first;
    const count = Math.max(2, Math.min(10, Math.floor(width / 80)));
    const step = Math.max(1, Math.ceil(span / count));
    const values: Array<number> = [];
    for (
      let year = Math.ceil(first / step) * step;
      year <= last;
      year += step
    ) {
      values.push(year);
    }
    return values;
  });
  const height = $derived(PAD.top + lane * 2 + BAND + AXIS + 4);

  function hover(event: PointerEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    let best: string | null = null;
    let distance = Infinity;
    for (const [side, entry] of lanes.entries()) {
      for (const race of entry.races) {
        const away = Math.hypot(
          sx(race.x) - x,
          (sy(side, race.share) - y) * 0.5,
        );
        if (away < distance) {
          distance = away;
          best = race.key;
        }
      }
    }
    active = best;
  }
</script>

<div class="relative overflow-hidden" bind:clientWidth={width}>
  {#if width > 0 && all.length > 0}
    <svg
      {width}
      {height}
      class="block touch-pan-y"
      aria-hidden="true"
      onpointermove={hover}
      onpointerdown={hover}
    >
      {#each [0, 1] as side (side)}
        {#each [0.5, 1] as share (share)}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(side, share)}
            y2={sy(side, share)}
            class="stroke-border"
            stroke-dasharray="2 3"
          />
          <text
            x={PAD.left - 6}
            y={sy(side, share)}
            text-anchor="end"
            dominant-baseline="middle"
            class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
            >{share * 100}</text
          >
        {/each}
      {/each}
      <rect
        x={PAD.left}
        y={middle - BAND / 2}
        width={width - PAD.left - PAD.right}
        height={BAND}
        class="fill-muted"
      />
      {#each lanes as entry, side (side)}
        {#each entry.terms as term, index (index)}
          <rect
            x={sx(term.from)}
            y={side === 0 ? middle - BAND / 2 + 2 : middle + 1}
            width={Math.max(2, sx(term.to) - sx(term.from))}
            height={BAND / 2 - 3}
            style:fill={term.color}
          />
        {/each}
      {/each}
      {#each links as link, index (index)}
        <line
          x1={sx(link.x)}
          x2={sx(link.x)}
          y1={sy(0, link.shares[0])}
          y2={sy(1, link.shares[1])}
          style:stroke={link.color}
          stroke-width="2"
          stroke-opacity="0.7"
        />
      {/each}
      {#each lanes as entry, side (side)}
        {#each entry.races as race (race.key)}
          {const r = $derived(size(race) + (race.key === active ? 1.5 : 0))}
          {const cx = $derived(sx(race.x))}
          {const cy = $derived(sy(side, race.share))}
          {#if side === 0}
            <circle
              {cx}
              {cy}
              {r}
              style:fill={race.isWin ? race.color : "var(--background)"}
              style:stroke={race.color}
              stroke-width="1.5"
              opacity={race.isPrimary ? 0.45 : 1}
            />
          {:else}
            <rect
              x={cx - r}
              y={cy - r}
              width={r * 2}
              height={r * 2}
              style:fill={race.isWin ? race.color : "var(--background)"}
              style:stroke={race.color}
              stroke-width="1.5"
              opacity={race.isPrimary ? 0.45 : 1}
            />
          {/if}
        {/each}
      {/each}
      {#each years as year (year)}
        <text
          x={sx(year)}
          y={height - 4}
          text-anchor="middle"
          class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
          >{year}</text
        >
      {/each}
    </svg>
    {#if active != null}
      <div
        class="pointer-events-none absolute inset-e-3 inset-bs-1 border border-border bg-background/95 px-2 py-1.5 text-xs shadow-sm max-inline-72"
      >
        {@render readout(active)}
      </div>
    {/if}
  {/if}
</div>
