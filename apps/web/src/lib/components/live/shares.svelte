<script lang="ts">
  import type { LiveFrame, LiveOption } from "@ea/analytics/live";
  import { count, percent } from "#lib/format.js";
  import { clock, tone } from "#lib/live/format.js";
  import { sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    options,
    order,
    frames,
    at,
    opens,
    onPick,
  }: {
    options: Array<LiveOption>;
    order: Array<number>;
    frames: Array<LiveFrame>;
    at: string | null;
    opens: number;
    onPick: (at: string | null) => void;
  } = $props();

  let width = $state(720);
  let hover = $state<number | null>(null);
  let tipWidth = $state(220);

  const LEFT = 44;
  const TOP = 16;
  const PLOT = 260;
  const H = TOP + PLOT + 30;
  const RIGHT = 64;

  const points = $derived(
    frames.flatMap((frame) => {
      const total = sum(frame.totals);
      return total > 0
        ? [
            {
              at: frame.at,
              time: Date.parse(frame.at),
              total,
              shares: frame.totals.map((value) => value / total),
            },
          ]
        : [];
    }),
  );
  const start = $derived.by(() => {
    const first = points[0]?.time ?? opens;
    return first - opens > 2 * 3_600_000
      ? first - 3_600_000
      : Math.min(opens, first);
  });
  const end = $derived(
    Math.max(
      opens + 5 * 3_600_000,
      (points.at(-1)?.time ?? opens) + 25 * 60_000,
    ),
  );
  const steady = $derived(
    points.filter(
      (point) => point.total >= 0.02 * Math.max(1, points.at(-1)?.total ?? 1),
    ),
  );
  const limit = $derived.by(() => {
    let top = 0.2;
    for (const point of steady) {
      for (const index of order) {
        top = Math.max(top, point.shares[index] ?? 0);
      }
    }
    const step = top > 0.5 ? 0.2 : 0.1;
    return Math.min(1, Math.ceil((top * 1.06) / step) * step);
  });
  const grid = $derived.by(() => {
    const step = limit > 0.5 ? 0.2 : 0.1;
    const marks: Array<number> = [];
    for (let value = step; value <= limit + 1e-9; value += step) {
      marks.push(value);
    }
    return marks;
  });

  const x = (time: number) =>
    LEFT + ((time - start) / Math.max(1, end - start)) * (width - LEFT - RIGHT);
  const y = (value: number) =>
    TOP + (1 - Math.min(limit, value) / limit) * PLOT;

  const lines = $derived(
    order.map((index) => ({
      index,
      color: tone(options[index]),
      d: steady
        .map(
          (point, step) =>
            `${step === 0 ? "M" : "L"}${x(point.time).toFixed(1)} ${y(point.shares[index] ?? 0).toFixed(1)}`,
        )
        .join(" "),
    })),
  );
  const tail = $derived(steady.at(-1) ?? null);
  const labels = $derived.by(() => {
    if (tail == null) {
      return [];
    }
    const list = order
      .map((index) => ({
        index,
        value: tail.shares[index] ?? 0,
        y: y(tail.shares[index] ?? 0),
      }))
      .toSorted((left, right) => left.y - right.y);
    for (const [step, label] of list.entries()) {
      const above = list[step - 1];
      if (above != null && label.y - above.y < 15) {
        label.y = above.y + 15;
      }
    }
    const overflow = (list.at(-1)?.y ?? 0) - (TOP + PLOT + 4);
    if (overflow > 0) {
      for (const label of list) {
        label.y -= overflow;
      }
    }
    return list;
  });
  const hours = $derived.by(() => {
    const marks: Array<number> = [];
    const step = end - start > 10 * 3_600_000 ? 2 * 3_600_000 : 3_600_000;
    for (let time = Math.ceil(start / step) * step; time <= end; time += step) {
      marks.push(time);
    }
    return marks;
  });

  const chosenIndex = $derived(
    at == null
      ? points.length - 1
      : points.findLastIndex((point) => point.at <= at),
  );
  const chosen = $derived(points[chosenIndex] ?? null);
  const focus = $derived(hover == null ? null : (points[hover] ?? null));

  function locate(event: MouseEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const position = ((event.clientX - box.left) / box.width) * width;
    let best = -1;
    let distance = Infinity;
    for (const [index, point] of points.entries()) {
      const gap = Math.abs(x(point.time) - position);
      if (gap < distance) {
        distance = gap;
        best = index;
      }
    }
    return best < 0 ? null : best;
  }
</script>

<figure class="grid gap-4">
  <figcaption class="grid gap-2.5">
    <span class="heading text-lg!">{m.night_shares_title()}</span>
    <ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {#each order as index (options[index]?.id ?? index)}
        <li class="flex items-center gap-1.5">
          <span
            class="shrink-0 rounded-full block-1 inline-4"
            style:background-color={tone(options[index])}
          ></span>
          {options[index]?.label ?? ""}
        </li>
      {/each}
    </ul>
  </figcaption>
  {#if steady.length === 0}
    <p class="text-sm text-muted-foreground">{m.board_trend_empty()}</p>
  {:else}
    <div class="relative" bind:clientWidth={width}>
      <svg
        viewBox="0 0 {width} {H}"
        class="block touch-pan-y overflow-visible inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-foreground"
        style:block-size="{H}px"
        role="slider"
        tabindex="0"
        aria-label={m.night_shares_aria()}
        aria-valuemin={0}
        aria-valuemax={Math.max(0, points.length - 1)}
        aria-valuenow={hover ?? Math.max(0, chosenIndex)}
        aria-valuetext={(focus ?? chosen) == null
          ? undefined
          : clock((focus ?? chosen)?.at ?? null)}
        onkeydown={(event) => {
          const last = points.length - 1;
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            hover = Math.max(
              0,
              Math.min(
                last,
                (hover ?? chosenIndex) + (event.key === "ArrowLeft" ? -1 : 1),
              ),
            );
          } else if (event.key === "Enter" && hover != null) {
            onPick(points[hover]?.at ?? null);
          } else if (event.key === "Escape") {
            hover = null;
            onPick(null);
          }
        }}
        onpointermove={(event) => (hover = locate(event))}
        onpointerleave={() => (hover = null)}
        onclick={(event) => {
          const index = locate(event);
          const point = index == null ? null : points[index];
          if (point != null) {
            onPick(point.at);
          }
        }}
      >
        {#each grid as value (value)}
          <line
            x1={LEFT}
            x2={width - RIGHT}
            y1={y(value)}
            y2={y(value)}
            stroke="var(--border)"
            stroke-dasharray="2 3"
          />
          <text
            x={LEFT - 8}
            y={y(value)}
            text-anchor="end"
            dominant-baseline="middle"
            class="fill-muted-foreground font-figures text-[11px] tabular-nums"
            >{percent(value, 0)}</text
          >
        {/each}
        <line
          x1={LEFT}
          x2={width - RIGHT}
          y1={y(0)}
          y2={y(0)}
          stroke="var(--foreground)"
          stroke-opacity="0.55"
        />
        {#each hours as time (time)}
          <line
            x1={x(time)}
            x2={x(time)}
            y1={y(0)}
            y2={y(0) + 4}
            stroke="var(--foreground)"
            stroke-opacity="0.55"
          />
          <text
            x={x(time)}
            y={y(0) + 17}
            text-anchor="middle"
            class="fill-muted-foreground font-figures text-[11px] tabular-nums"
            >{clock(time)}</text
          >
        {/each}

        {#each lines.toReversed() as line (line.index)}
          <path
            d={line.d}
            fill="none"
            stroke={line.color}
            stroke-width={line.index === order[0] ? 2.75 : 2}
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        {/each}

        {#if tail != null}
          {#each labels as label (label.index)}
            {const color = $derived(tone(options[label.index]))}
            <circle
              cx={x(tail.time)}
              cy={y(label.value)}
              r="3.5"
              fill={color}
              stroke="var(--background)"
              stroke-width="1.5"
            />
            <text
              x={x(tail.time) + 10}
              y={label.y}
              dominant-baseline="middle"
              class="font-figures text-[12px] tabular-nums"
              font-weight={label.index === order[0] ? 700 : 500}
              style:fill={color}>{percent(label.value)}</text
            >
          {/each}
        {/if}

        {#if chosen != null && at != null}
          <line
            x1={x(chosen.time)}
            x2={x(chosen.time)}
            y1={TOP - 4}
            y2={y(0)}
            stroke="var(--foreground)"
            stroke-dasharray="3 3"
          />
        {/if}
        {#if focus != null}
          <line
            x1={x(focus.time)}
            x2={x(focus.time)}
            y1={TOP}
            y2={y(0)}
            stroke="var(--foreground)"
            stroke-opacity="0.4"
          />
          {#each order as index (index)}
            <circle
              cx={x(focus.time)}
              cy={y(focus.shares[index] ?? 0)}
              r="3"
              fill="var(--background)"
              stroke={tone(options[index])}
              stroke-width="2"
            />
          {/each}
        {/if}
      </svg>
      {#if focus != null}
        {const left = $derived(
          x(focus.time) + 14 + tipWidth > width
            ? x(focus.time) - 14 - tipWidth
            : x(focus.time) + 14,
        )}
        {const ranked = $derived(
          order.toSorted(
            (left, right) =>
              (focus.shares[right] ?? 0) - (focus.shares[left] ?? 0),
          ),
        )}
        <div
          class="pointer-events-none absolute z-10 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-[min(18rem,80vw)]"
          style:inset-inline-start="{Math.max(0, left)}px"
          style:inset-block-start="{TOP}px"
          bind:offsetWidth={tipWidth}
        >
          <span class="font-figures font-semibold tabular-nums"
            >{clock(focus.at)}</span
          >
          <span class="grid grid-cols-[auto_1fr_auto] gap-x-2 gap-y-1 text-xs">
            {#each ranked as index, rank (index)}
              <span
                class="mbs-1 shrink-0 rounded-full block-2 inline-2"
                style:background-color={tone(options[index])}
              ></span>
              <span class={cn("text-pretty", { "font-semibold": rank === 0 })}
                >{options[index]?.label ?? ""}</span
              >
              <span
                class={cn("text-end font-figures tabular-nums", {
                  "font-semibold": rank === 0,
                })}>{percent(focus.shares[index] ?? 0)}</span
              >
            {/each}
            <span></span>
            <span class="text-muted-foreground">{m.night_trend_votes()}</span>
            <span class="text-end font-figures tabular-nums"
              >{count(focus.total)}</span
            >
          </span>
        </div>
      {/if}
    </div>
  {/if}
</figure>
