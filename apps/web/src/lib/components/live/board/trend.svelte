<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import { percent } from "#lib/format.js";
  import { clock, surname, tone } from "#lib/live/format.js";
  import { ranked, sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";

  const { race }: { race: LiveRace } = $props();

  let width = $state(0);
  let height = $state(0);

  const PAD = { top: 6, right: 44, bottom: 16, left: 4 };

  const frames = $derived(race.frames.filter((frame) => sum(frame.totals) > 0));
  const top = $derived(ranked(frames.at(-1)?.totals ?? []).slice(0, 2));
  const series = $derived(
    top.map((index) => ({
      option: race.options[index],
      points: frames.map((frame) => ({
        at: Date.parse(frame.at),
        share: (frame.totals[index] ?? 0) / Math.max(1, sum(frame.totals)),
      })),
    })),
  );
  const range = $derived.by(() => {
    let low = 1;
    let high = 0;
    for (const line of series) {
      for (const point of line.points) {
        low = Math.min(low, point.share);
        high = Math.max(high, point.share);
      }
    }
    const pad = Math.max(0.02, (high - low) * 0.12);
    return { low: Math.max(0, low - pad), high: Math.min(1, high + pad) };
  });
  const span = $derived({
    from: series[0]?.points[0]?.at ?? 0,
    to: series[0]?.points.at(-1)?.at ?? 1,
  });
  const inner = $derived({
    w: Math.max(1, width - PAD.left - PAD.right),
    h: Math.max(1, height - PAD.top - PAD.bottom),
  });

  function x(at: number) {
    return (
      PAD.left + ((at - span.from) / Math.max(1, span.to - span.from)) * inner.w
    );
  }

  function y(share: number) {
    return (
      PAD.top +
      (1 - (share - range.low) / Math.max(0.001, range.high - range.low)) *
        inner.h
    );
  }
</script>

<div
  class="relative block-full"
  bind:clientWidth={width}
  bind:clientHeight={height}
>
  {#if frames.length < 2}
    <p class="text-[0.75em] text-muted-foreground">{m.board_trend_empty()}</p>
  {:else if width > 0 && height > 0}
    <svg
      viewBox="0 0 {width} {height}"
      class="absolute inset-0 overflow-visible"
      role="img"
      aria-label={m.board_kind_trend()}
    >
      {#if range.low < 0.5 && range.high > 0.5}
        <line
          x1={PAD.left}
          x2={PAD.left + inner.w}
          y1={y(0.5)}
          y2={y(0.5)}
          class="stroke-border"
          stroke-dasharray="2 3"
        />
      {/if}
      {#each series as line (line.option?.id)}
        {const last = $derived(line.points.at(-1))}
        <polyline
          points={line.points
            .map(
              (point) =>
                `${x(point.at).toFixed(1)},${y(point.share).toFixed(1)}`,
            )
            .join(" ")}
          fill="none"
          stroke={tone(line.option)}
          stroke-width="1.75"
          stroke-linejoin="round"
        />
        {#if last != null}
          <circle
            cx={x(last.at)}
            cy={y(last.share)}
            r="2.5"
            fill={tone(line.option)}
          />
          <text
            x={x(last.at) + 6}
            y={y(last.share)}
            dominant-baseline="central"
            class="font-figures text-[10px] font-semibold tabular-nums"
            fill={tone(line.option)}
            >{percent(last.share, 1)}<title>{surname(line.option)}</title></text
          >
        {/if}
      {/each}
      <text
        x={PAD.left}
        y={height - 3}
        class="fill-muted-foreground font-figures text-[9px] tabular-nums"
        >{clock(span.from)}</text
      >
      <text
        x={PAD.left + inner.w}
        y={height - 3}
        text-anchor="end"
        class="fill-muted-foreground font-figures text-[9px] tabular-nums"
        >{clock(span.to)}</text
      >
    </svg>
  {/if}
</div>
