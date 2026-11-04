<script lang="ts">
  import { untrack } from "svelte";
  import { percent } from "#lib/format.js";
  import { odds } from "#lib/live/nation.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { signed } from "./scale.js";

  const {
    final,
    points,
    blue,
    red,
  }: {
    final: number;
    points: Array<Array<number>>;
    blue: string;
    red: string;
  } = $props();

  const LOW = -0.25;
  const HIGH = 0.1;
  const LEFT = 44;
  const RIGHT = 14;
  const TOP = 14;
  const BOTTOM = 30;

  let width = $state(720);
  let index = $state(
    untrack(() =>
      Math.max(
        0,
        points.findIndex((point) => (point[0] ?? 0) >= 0.4),
      ),
    ),
  );

  const height = $derived(width < 520 ? 260 : 340);
  const x = (share: number) => LEFT + share * (width - LEFT - RIGHT);
  const y = (margin: number) =>
    TOP + ((HIGH - margin) / (HIGH - LOW)) * (height - TOP - BOTTOM);
  const rows = $derived(
    points.map(
      ([
        share = 0,
        raw = 0,
        low = 0,
        mid = 0,
        high = 0,
        chance = 0.5,
        live = 0,
      ]) => ({ share, raw, low, mid, high, chance, isLive: live === 1 }),
    ),
  );
  const live = $derived(rows.filter((row) => row.isLive));
  const band = $derived(
    live.length === 0
      ? ""
      : `M${live.map((row) => `${x(row.share)},${y(row.high)}`).join("L")}L${live
          .toReversed()
          .map((row) => `${x(row.share)},${y(row.low)}`)
          .join("L")}Z`,
  );
  const middle = $derived(
    `M${live.map((row) => `${x(row.share)},${y(row.mid)}`).join("L")}`,
  );
  const counted = $derived(
    `M${rows.map((row) => `${x(row.share)},${y(Math.max(LOW - 0.05, row.raw))}`).join("L")}`,
  );
  const ticks = [-0.2, -0.1, 0, 0.1];
  const shares = [0, 0.25, 0.5, 0.75, 1];
  const now = $derived(rows[index] ?? rows[0]);
  const favored = $derived(now == null ? null : now.chance >= 0.5 ? "D" : "R");

  function pick(event: PointerEvent) {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const share = (event.clientX - box.left - LEFT) / (width - LEFT - RIGHT);
    let best = 0;
    for (const [position, row] of rows.entries()) {
      if (
        Math.abs(row.share - share) < Math.abs((rows[best]?.share ?? 0) - share)
      ) {
        best = position;
      }
    }
    index = best;
  }
</script>

<figure class="grid gap-4">
  <div class="relative" bind:clientWidth={width}>
    <svg
      viewBox="0 0 {width} {height}"
      class="block touch-pan-y overflow-visible inline-full"
      style:block-size="{height}px"
      aria-hidden="true"
      onpointermove={pick}
      onpointerdown={pick}
    >
      <defs>
        <clipPath id="replay-plot">
          <rect
            x={LEFT}
            y={TOP}
            width={width - LEFT - RIGHT}
            height={height - TOP - BOTTOM}
          />
        </clipPath>
      </defs>
      {#each ticks as tick (tick)}
        <line
          x1={LEFT}
          x2={width - RIGHT}
          y1={y(tick)}
          y2={y(tick)}
          class={cn({
            "stroke-foreground/50": tick === 0,
            "stroke-border": tick !== 0,
          })}
          stroke-width="1"
        />
        <text
          x={LEFT - 8}
          y={y(tick)}
          dy="0.32em"
          text-anchor="end"
          class="font-figures text-[0.6875rem] tabular-nums"
          fill={tick === 0 ? "var(--muted-foreground)" : tick > 0 ? blue : red}
          >{signed(tick, m.method_even())}</text
        >
      {/each}
      {#each shares as share (share)}
        <text
          x={x(share)}
          y={height - 10}
          text-anchor={share === 0 ? "start" : share === 1 ? "end" : "middle"}
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{percent(share, 0)}</text
        >
      {/each}
      <g clip-path="url(#replay-plot)">
        <path d={band} fill="var(--foreground)" fill-opacity="0.11" />
        <path
          d={counted}
          fill="none"
          stroke="var(--muted-foreground)"
          stroke-width="2"
          stroke-dasharray="0.1 4.5"
          stroke-linecap="round"
        />
        <line
          x1={LEFT}
          x2={width - RIGHT}
          y1={y(final)}
          y2={y(final)}
          stroke={blue}
          stroke-width="1.5"
          stroke-dasharray="6 4"
        />
        <path
          d={middle}
          fill="none"
          stroke="var(--foreground)"
          stroke-width="2.25"
          stroke-linejoin="round"
        />
      </g>
      <text
        x={width - RIGHT}
        y={y(final) - 7}
        text-anchor="end"
        class="text-[0.75rem] font-semibold"
        fill={blue}
        >{m.method_hero_final()} {signed(final, m.method_even())}</text
      >
      {#if now != null}
        <line
          x1={x(now.share)}
          x2={x(now.share)}
          y1={TOP}
          y2={height - BOTTOM}
          class="stroke-foreground"
          stroke-width="1"
        />
        {#if now.isLive}
          <circle
            cx={x(now.share)}
            cy={y(now.mid)}
            r="4.5"
            class="fill-background stroke-foreground"
            stroke-width="2"
          />
        {/if}
      {/if}
    </svg>
  </div>

  <div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
    <label class="grid gap-1.5">
      <span class="text-xs text-muted-foreground">{m.method_hero_scrub()}</span>
      <input
        type="range"
        min="0"
        max={rows.length - 1}
        step="1"
        bind:value={index}
        class="accent-foreground inline-full"
        aria-valuetext={now == null
          ? ""
          : m.method_hero_at({ share: percent(now.share, 0) })}
      />
    </label>
    <ul
      class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
      aria-hidden="true"
    >
      <li class="flex items-center gap-1.5">
        <svg viewBox="0 0 20 6" class="block-1.5 inline-5"
          ><line
            x1="0"
            x2="20"
            y1="3"
            y2="3"
            stroke="currentColor"
            stroke-width="2.5"
            class="text-foreground"
          /></svg
        >{m.method_hero_estimate()}
      </li>
      <li class="flex items-center gap-1.5">
        <span class="rounded-xs bg-foreground/15 block-2.5 inline-5"
        ></span>{m.method_hero_band()}
      </li>
      <li class="flex items-center gap-1.5">
        <svg viewBox="0 0 20 6" class="block-1.5 inline-5"
          ><line
            x1="1"
            x2="20"
            y1="3"
            y2="3"
            stroke="currentColor"
            stroke-width="2"
            stroke-dasharray="0.1 4"
            stroke-linecap="round"
          /></svg
        >{m.method_hero_counted()}
      </li>
    </ul>
  </div>

  {#if now != null}
    <dl
      class="grid grid-cols-2 gap-px overflow-clip border-bs-[3px] border-foreground bg-border sm:grid-cols-4"
      aria-live="polite"
    >
      <div class="grid gap-1 bg-background py-2.5 pe-3">
        <dt class="text-xs text-muted-foreground">{m.method_hero_scrub()}</dt>
        <dd class="font-figures text-xl font-semibold tabular-nums">
          {percent(now.share, 0)}
        </dd>
      </div>
      <div class="grid gap-1 bg-background py-2.5 pe-3 sm:ps-3">
        <dt class="text-xs text-muted-foreground">{m.method_hero_count()}</dt>
        <dd
          class="font-figures text-xl font-semibold tabular-nums"
          style:color={now.raw >= 0 ? blue : red}
        >
          {signed(now.raw, m.method_even())}
        </dd>
      </div>
      {#if now.isLive}
        <div class="grid gap-1 bg-background py-2.5 pe-3 sm:ps-3">
          <dt class="text-xs text-muted-foreground">
            {m.method_hero_middle()}
          </dt>
          <dd class="grid">
            <span
              class="font-figures text-xl font-semibold tabular-nums"
              style:color={now.mid >= 0 ? blue : red}
              >{signed(now.mid, m.method_even())}</span
            >
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{m.method_range({
                low: signed(now.low, m.method_even()),
                high: signed(now.high, m.method_even()),
              })}</span
            >
          </dd>
        </div>
        <div class="grid gap-1 bg-background py-2.5 pe-3 sm:ps-3">
          <dt class="text-xs text-muted-foreground">
            {m.method_hero_chance()}
          </dt>
          <dd
            class="font-figures text-xl font-semibold tabular-nums"
            style:color={favored === "D" ? blue : red}
          >
            {favored}
            {odds(Math.max(now.chance, 1 - now.chance))}
          </dd>
        </div>
      {:else}
        <div
          class="col-span-2 grid content-center bg-background py-2.5 sm:ps-3"
        >
          <p class="text-sm text-muted-foreground">{m.method_hero_early()}</p>
        </div>
      {/if}
    </dl>
  {/if}
</figure>
