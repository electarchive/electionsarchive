<script lang="ts">
  import type { LiveFrame, LiveOption } from "@ea/analytics/live";
  import type { Point } from "#lib/live/history.js";
  import { count, percent } from "#lib/format.js";
  import { clock, lead, surname, timeMarks, tone } from "#lib/live/format.js";
  import { sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import MethodLink from "./method-link.svelte";

  const {
    options,
    pair,
    frames,
    points: estimates,
    expected,
    at,
    opens,
    onPick,
  }: {
    options: Array<LiveOption>;
    pair: [number, number];
    frames: Array<LiveFrame>;
    points: Array<Point>;
    expected: number | null;
    at: string | null;
    opens: number;
    onPick: (at: string | null) => void;
  } = $props();

  let width = $state(720);
  let hover = $state<number | null>(null);
  let tipWidth = $state(200);

  const LEFT = 52;
  const TOP = 22;
  const MARGIN = 236;
  const GAP = 34;
  const SHARE = 72;
  const H = TOP + MARGIN + GAP + SHARE + 26;
  const shareTop = TOP + MARGIN + GAP;

  const a = $derived(options[pair[0]]);
  const b = $derived(options[pair[1]]);
  const toneA = $derived(tone(a));
  const toneB = $derived(tone(b));
  const isWide = $derived(width >= 600);
  const right = $derived(isWide ? 150 : 14);

  const points = $derived(
    frames.map((frame, index) => {
      const total = sum(frame.totals);
      const found = estimates[index];
      return {
        at: frame.at,
        time: Date.parse(frame.at),
        total,
        signed:
          total > 0
            ? ((frame.totals[pair[0]] ?? 0) - (frame.totals[pair[1]] ?? 0)) /
              total
            : null,
        share:
          found?.share ??
          (expected != null && expected > 0
            ? Math.min(0.99, total / expected)
            : null),
        band: found?.isLive === true ? found : null,
      };
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
      (point) =>
        point.signed != null &&
        point.total >=
          0.02 * Math.max(1, expected ?? points.at(-1)?.total ?? 1),
    ),
  );
  const limit = $derived.by(() => {
    let top = 0.05;
    for (const point of steady) {
      top = Math.max(top, Math.abs(point.signed ?? 0));
      if (point.band != null) {
        top = Math.max(
          top,
          Math.abs(point.band.low),
          Math.abs(point.band.high),
        );
      }
    }
    const step = top > 0.3 ? 0.1 : top > 0.12 ? 0.05 : 0.02;
    return Math.ceil((top * 1.08) / step) * step;
  });
  const grid = $derived.by(() => {
    const step = limit > 0.3 ? 0.1 : limit > 0.12 ? 0.05 : 0.02;
    const marks: Array<number> = [];
    for (let value = step; value < limit - 1e-9; value += step) {
      marks.push(value, -value);
    }
    return marks;
  });

  const x = (time: number) =>
    LEFT + ((time - start) / Math.max(1, end - start)) * (width - LEFT - right);
  const y = (value: number) =>
    TOP +
    ((limit - Math.max(-limit, Math.min(limit, value))) / (2 * limit)) * MARGIN;
  const ys = (value: number) => shareTop + (1 - value) * SHARE;
  const zero = $derived(y(0));

  function path(list: Array<{ time: number; value: number }>) {
    return list
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${x(point.time).toFixed(1)} ${y(point.value).toFixed(1)}`,
      )
      .join(" ");
  }
  const counted = $derived(
    path(
      steady.map((point) => ({ time: point.time, value: point.signed ?? 0 })),
    ),
  );
  const live = $derived(steady.filter((point) => point.band != null));
  const middle = $derived(
    path(
      live.map((point) => ({ time: point.time, value: point.band?.mid ?? 0 })),
    ),
  );
  const ribbon = $derived.by(() => {
    if (live.length < 2) {
      return "";
    }
    const upper = live.map(
      (point, index) =>
        `${index === 0 ? "M" : "L"}${x(point.time).toFixed(1)} ${y(point.band?.high ?? 0).toFixed(1)}`,
    );
    const lower = live
      .toReversed()
      .map(
        (point) =>
          `L${x(point.time).toFixed(1)} ${y(point.band?.low ?? 0).toFixed(1)}`,
      );
    return `${upper.join(" ")} ${lower.join(" ")} Z`;
  });
  const area = $derived.by(() => {
    const list = points.filter((point) => point.share != null);
    if (list.length === 0) {
      return { line: "", fill: "" };
    }
    const line = list
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${x(point.time).toFixed(1)} ${ys(point.share ?? 0).toFixed(1)}`,
      )
      .join(" ");
    const first = x(list[0]?.time ?? start).toFixed(1);
    const last = x(list.at(-1)?.time ?? start).toFixed(1);
    return {
      line,
      fill: `M${first} ${ys(0)} ${line.replace(/^M/u, "L")} L${last} ${ys(0)} Z`,
    };
  });
  const marks = $derived(timeMarks(start, end, width - LEFT - right));

  const chosenIndex = $derived(
    at == null
      ? points.length - 1
      : points.findLastIndex((point) => point.at <= at),
  );
  const chosen = $derived(points[chosenIndex] ?? null);
  const focus = $derived(hover == null ? chosen : (points[hover] ?? null));
  const tail = $derived(steady.at(-1) ?? null);
  const tailLive = $derived(live.at(-1) ?? null);
  const name = (value: number) => (value >= 0 ? surname(a) : surname(b));
  const ink = (value: number) => (value >= 0 ? toneA : toneB);
  const labels = $derived.by(() => {
    const list: Array<{
      id: string;
      text: string;
      value: number;
      y: number;
      isEstimate: boolean;
    }> = [];
    if (tail?.signed != null) {
      list.push({
        id: "counted",
        text: m.night_trend_end_counted({
          name: name(tail.signed),
          margin: lead(tail.signed),
        }),
        value: tail.signed,
        y: y(tail.signed),
        isEstimate: false,
      });
    }
    if (tailLive?.band != null && tailLive === tail) {
      const mid = tailLive.band.mid;
      list.push({
        id: "estimate",
        text: m.night_trend_end_estimate({
          name: name(mid),
          margin: lead(mid),
        }),
        value: mid,
        y: y(mid),
        isEstimate: true,
      });
    }
    if (list.length === 2) {
      const [first, second] = list.toSorted((left, other) => left.y - other.y);
      if (first != null && second != null && second.y - first.y < 30) {
        const center = (first.y + second.y) / 2;
        first.y = center - 15;
        second.y = center + 15;
      }
    }
    return list;
  });

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
  <figcaption class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
    <span class="flex items-center gap-1.5">
      <span class="heading text-lg!">{m.night_trend_title()}</span>
      <MethodLink section="ranges" />
    </span>
    <span
      class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground"
    >
      {#if live.length > 0}
        <span class="flex items-center gap-1.5">
          <svg viewBox="0 0 20 6" class="block-1.5 inline-5" aria-hidden="true"
            ><line
              x1="0"
              x2="20"
              y1="3"
              y2="3"
              stroke="currentColor"
              stroke-width="2.5"
            /></svg
          >
          {m.night_trend_estimate()}
        </span>
      {/if}
      <span class="flex items-center gap-1.5">
        <svg viewBox="0 0 20 6" class="block-1.5 inline-5" aria-hidden="true"
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
        >
        {m.night_trend_counted()}
      </span>
      {#if live.length > 0}
        <span class="flex items-center gap-1.5">
          <span
            class="rounded-xs block-2.5 inline-5"
            style:background="linear-gradient(to bottom, color-mix(in oklab, {toneA}
            28%, transparent) 50%, color-mix(in oklab, {toneB} 28%, transparent) 50%)"
          ></span>
          {m.night_trend_band()}
        </span>
      {/if}
    </span>
  </figcaption>
  <div class="relative" bind:clientWidth={width}>
    <svg
      viewBox="0 0 {width} {H}"
      class="block touch-pan-y overflow-visible inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-foreground"
      style:block-size="{H}px"
      role="slider"
      tabindex="0"
      aria-label={m.night_trend_aria()}
      aria-valuemin={0}
      aria-valuemax={Math.max(0, points.length - 1)}
      aria-valuenow={hover ?? Math.max(0, chosenIndex)}
      aria-valuetext={focus == null ? undefined : clock(focus.at)}
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
      <defs>
        <clipPath id="trend-above"
          ><rect x="0" y="0" {width} height={zero} /></clipPath
        >
        <clipPath id="trend-below"
          ><rect
            x="0"
            y={zero}
            {width}
            height={TOP + MARGIN - zero + 4}
          /></clipPath
        >
      </defs>

      <rect
        x={LEFT}
        y={TOP}
        width={Math.max(0, width - LEFT - right)}
        height={zero - TOP}
        fill={toneA}
        fill-opacity="0.035"
      />
      <rect
        x={LEFT}
        y={zero}
        width={Math.max(0, width - LEFT - right)}
        height={TOP + MARGIN - zero}
        fill={toneB}
        fill-opacity="0.035"
      />
      {#each grid as value (value)}
        <line
          x1={LEFT}
          x2={width - right}
          y1={y(value)}
          y2={y(value)}
          stroke="var(--border)"
        />
        <text
          x={LEFT - 8}
          y={y(value)}
          text-anchor="end"
          dominant-baseline="middle"
          class="font-figures text-[11px] tabular-nums"
          style:fill={ink(value)}>+{lead(Math.abs(value), 0)}</text
        >
      {/each}
      <line
        x1={LEFT}
        x2={width - right}
        y1={zero}
        y2={zero}
        stroke="var(--foreground)"
        stroke-opacity="0.55"
      />
      <text
        x={LEFT - 8}
        y={zero}
        text-anchor="end"
        dominant-baseline="middle"
        class="fill-muted-foreground text-[11px]">{m.night_trend_even()}</text
      >
      <text
        x={LEFT + 6}
        y={TOP + 12}
        class="text-[11px] font-semibold"
        style:fill={toneA}>{surname(a)}</text
      >
      <text
        x={LEFT + 6}
        y={TOP + MARGIN - 8}
        class="text-[11px] font-semibold"
        style:fill={toneB}>{surname(b)}</text
      >

      {#if ribbon !== ""}
        <path
          d={ribbon}
          fill={toneA}
          fill-opacity="0.2"
          clip-path="url(#trend-above)"
        />
        <path
          d={ribbon}
          fill={toneB}
          fill-opacity="0.2"
          clip-path="url(#trend-below)"
        />
      {/if}
      <path
        d={counted}
        fill="none"
        stroke={toneA}
        stroke-width="2.25"
        stroke-dasharray="0.1 4.5"
        stroke-linecap="round"
        clip-path="url(#trend-above)"
      />
      <path
        d={counted}
        fill="none"
        stroke={toneB}
        stroke-width="2.25"
        stroke-dasharray="0.1 4.5"
        stroke-linecap="round"
        clip-path="url(#trend-below)"
      />
      {#if middle !== ""}
        <path
          d={middle}
          fill="none"
          stroke={toneA}
          stroke-width="2.75"
          stroke-linejoin="round"
          clip-path="url(#trend-above)"
        />
        <path
          d={middle}
          fill="none"
          stroke={toneB}
          stroke-width="2.75"
          stroke-linejoin="round"
          clip-path="url(#trend-below)"
        />
      {/if}

      {#if isWide}
        {#each labels as label (label.id)}
          <circle
            cx={x(tail?.time ?? start)}
            cy={y(label.value)}
            r={label.isEstimate ? 4 : 3}
            fill={label.isEstimate ? ink(label.value) : "var(--background)"}
            stroke={ink(label.value)}
            stroke-width="2"
          />
          <text
            x={x(tail?.time ?? start) + 10}
            y={label.y}
            dominant-baseline="middle"
            class="font-figures text-[12px] tabular-nums"
            style:fill={ink(label.value)}
            font-weight={label.isEstimate ? 700 : 500}>{label.text}</text
          >
        {/each}
      {/if}

      <text
        x={LEFT}
        y={shareTop - 10}
        class="fill-foreground text-[12px] font-semibold"
        >{m.night_trend_share_title()}</text
      >
      {#each [0.25, 0.5, 0.75, 1] as tick (tick)}
        <line
          x1={LEFT}
          x2={width - right}
          y1={ys(tick)}
          y2={ys(tick)}
          stroke="var(--border)"
          stroke-dasharray={tick === 1 ? undefined : "2 3"}
        />
        <text
          x={LEFT - 8}
          y={ys(tick)}
          text-anchor="end"
          dominant-baseline="middle"
          class="fill-muted-foreground font-figures text-[11px] tabular-nums"
          >{percent(tick, 0)}</text
        >
      {/each}
      {#if area.fill !== ""}
        <path d={area.fill} fill="var(--foreground)" fill-opacity="0.12" />
        <path
          d={area.line}
          fill="none"
          stroke="var(--foreground)"
          stroke-opacity="0.7"
          stroke-width="1.5"
          stroke-linejoin="round"
        />
      {/if}
      {#if isWide && points.at(-1)?.share != null}
        {const last = $derived(points.at(-1))}
        <text
          x={x(last?.time ?? start) + 10}
          y={ys(last?.share ?? 0)}
          dominant-baseline="middle"
          class="fill-foreground font-figures text-[12px] font-semibold tabular-nums"
          >{percent(last?.share ?? 0, 0)}</text
        >
      {/if}
      <line
        x1={LEFT}
        x2={width - right}
        y1={ys(0)}
        y2={ys(0)}
        stroke="var(--foreground)"
        stroke-opacity="0.55"
      />

      {#each marks as mark (mark.at)}
        <line
          x1={x(mark.at)}
          x2={x(mark.at)}
          y1={ys(0)}
          y2={ys(0) + 4}
          stroke="var(--foreground)"
          stroke-opacity="0.55"
        />
        <text
          x={x(mark.at)}
          y={ys(0) + 17}
          text-anchor="middle"
          class="fill-muted-foreground font-figures text-[11px] tabular-nums"
          >{mark.label}</text
        >
      {/each}

      {#if chosen != null && at != null}
        <line
          x1={x(chosen.time)}
          x2={x(chosen.time)}
          y1={TOP - 6}
          y2={ys(0)}
          stroke="var(--foreground)"
          stroke-dasharray="3 3"
        />
        <text
          x={Math.min(width - 44, Math.max(44, x(chosen.time)))}
          y={TOP - 10}
          text-anchor="middle"
          class="fill-foreground font-figures text-[11px] font-semibold tabular-nums"
          >{clock(chosen.at)}</text
        >
      {/if}
      {#if hover != null && focus != null}
        <line
          x1={x(focus.time)}
          x2={x(focus.time)}
          y1={TOP}
          y2={ys(0)}
          stroke="var(--foreground)"
          stroke-opacity="0.4"
        />
        {#if focus.signed != null}
          <circle
            cx={x(focus.time)}
            cy={y(focus.signed)}
            r="3.5"
            fill="var(--background)"
            stroke={ink(focus.signed)}
            stroke-width="2"
          />
        {/if}
        {#if focus.band != null}
          <circle
            cx={x(focus.time)}
            cy={y(focus.band.mid)}
            r="4"
            fill={ink(focus.band.mid)}
            stroke="var(--background)"
            stroke-width="1.5"
          />
        {/if}
        {#if focus.share != null}
          <circle
            cx={x(focus.time)}
            cy={ys(focus.share)}
            r="3"
            fill="var(--foreground)"
          />
        {/if}
      {/if}
    </svg>
    {#if hover != null && focus != null}
      {const left = $derived(
        x(focus.time) + 14 + tipWidth > width
          ? x(focus.time) - 14 - tipWidth
          : x(focus.time) + 14,
      )}
      <div
        class="pointer-events-none absolute z-10 grid gap-1 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm min-inline-48"
        style:inset-inline-start="{Math.max(0, left)}px"
        style:inset-block-start="{TOP}px"
        bind:offsetWidth={tipWidth}
      >
        <span class="font-figures font-semibold tabular-nums"
          >{clock(focus.at)}</span
        >
        <span class="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 text-xs">
          {#if focus.band != null}
            <span class="text-muted-foreground">{m.night_trend_estimate()}</span
            >
            <span
              class="text-end font-figures font-semibold tabular-nums"
              style:color={ink(focus.band.mid)}
              >{name(focus.band.mid)} +{lead(focus.band.mid)}</span
            >
            <span></span>
            <span
              class="text-end font-figures text-muted-foreground tabular-nums"
            >
              {m.night_field_range({
                low: `${focus.band.low >= 0 ? "+" : "−"}${lead(focus.band.low)}`,
                high: `${focus.band.high >= 0 ? "+" : "−"}${lead(focus.band.high)}`,
              })}
            </span>
          {/if}
          {#if focus.signed != null}
            <span class="text-muted-foreground">{m.night_trend_counted()}</span>
            <span
              class="text-end font-figures tabular-nums"
              style:color={ink(focus.signed)}
              >{name(focus.signed)} +{lead(focus.signed)}</span
            >
          {/if}
          <span class="text-muted-foreground">{m.night_trend_votes()}</span>
          <span class="text-end font-figures tabular-nums"
            >{count(focus.total)}{focus.share == null
              ? ""
              : ` · ${percent(focus.share, 0)}`}</span
          >
        </span>
      </div>
    {/if}
    {#if !isWide && labels.length > 0}
      <p class="flex flex-wrap gap-x-4 text-xs">
        {#each labels as label (label.id)}
          <span
            class={cn("font-figures tabular-nums", {
              "font-semibold": label.isEstimate,
            })}
            style:color={ink(label.value)}>{label.text}</span
          >
        {/each}
      </p>
    {/if}
  </div>
</figure>
