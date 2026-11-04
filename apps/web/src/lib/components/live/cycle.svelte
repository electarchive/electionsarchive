<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import { resolve } from "$app/paths";
  import type { Cycle, CycleLine } from "#lib/live/cycle.js";
  import { percent } from "#lib/format.js";
  import {
    axisOf,
    baseline,
    nowMargin,
    pastMargin,
    projectedMargin,
    tags,
    tint,
  } from "#lib/live/cycle.js";
  import { lead } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    path,
    options,
    totals,
    total,
    counted = null,
    estimate = null,
    class: className,
  }: {
    path: string;
    options: Array<LiveOption>;
    totals: Array<number>;
    total: number;
    counted?: number | null;
    estimate?: LiveEstimate | null;
    class?: string;
  } = $props();

  const id = $props.id();

  let cycle = $state<Cycle | null>(null);

  $effect(() => {
    const wanted = path;
    let isStale = false;
    orpc.live.cycle
      .call({ path: wanted })
      .then((value) => {
        if (!isStale) {
          cycle = value;
        }
      })
      .catch(() => {
        if (!isStale) {
          cycle = null;
        }
      });
    return () => {
      isStale = true;
      cycle = null;
    };
  });

  const year = $derived(cycle?.date.slice(0, 4) ?? "");
  const axis = $derived(cycle == null ? null : axisOf(cycle));
  const marks = $derived(axis == null ? null : tags(axis));
  const reference = $derived(cycle == null ? null : baseline(cycle));
  const past = $derived.by(() => {
    if (axis == null || cycle == null) {
      return [];
    }
    const ordered = [
      ...cycle.lines.filter((line) => line === reference),
      ...cycle.lines.filter((line) => line !== reference),
    ];
    return ordered.flatMap((line, index) => {
      const value = pastMargin(line, axis);
      return value == null ? [] : [{ line, value, row: index }];
    });
  });
  const start = $derived(past[0]?.value ?? null);
  const current = $derived(
    axis == null ? null : nowMargin(options, totals, total, axis),
  );
  const projected = $derived(
    axis == null ? null : projectedMargin(options, estimate, axis),
  );
  const scale = $derived.by(() => {
    let widest = 0;
    for (const value of [
      ...past.map((entry) => entry.value),
      current,
      projected?.low,
      projected?.high,
    ]) {
      if (value != null) {
        widest = Math.max(widest, Math.abs(value));
      }
    }
    return Math.min(1, Math.max(0.1, Math.ceil((widest + 0.02) * 10) / 10));
  });

  function show(value: number) {
    if (axis == null || marks == null || Math.abs(value) < 0.0005) {
      return { text: m.night_cycle_even(), color: "var(--foreground)" };
    }
    return value > 0
      ? { text: `${marks.right}+${lead(value)}`, color: tint(axis.right) }
      : { text: `${marks.left}+${lead(value)}`, color: tint(axis.left) };
  }

  function shift(value: number) {
    if (axis == null || start == null) {
      return null;
    }
    const delta = value - start;
    if (Math.abs(delta) < 0.0005) {
      return { text: m.night_cycle_same(), color: "var(--muted-foreground)" };
    }
    const party = delta > 0 ? axis.right : axis.left;
    return {
      text: m.night_cycle_shift({ points: lead(delta), party: party.short }),
      color: tint(party),
    };
  }

  function place(value: number, edge = 0) {
    const at = 0.5 + (Math.max(-scale, Math.min(scale, value)) / scale) * 0.5;
    return `${Math.max(edge, Math.min(1 - edge, at)) * 100}%`;
  }

  function sign(line: CycleLine) {
    if (line.isEstimated) {
      return "*";
    }
    return line.isRedrawn ? "†" : null;
  }

  const notes = $derived([
    ...(past.some((entry) => entry.line.isEstimated)
      ? [{ sign: "*", text: m.night_cycle_estimated() }]
      : []),
    ...(past.some((entry) => entry.line.isRedrawn && !entry.line.isEstimated)
      ? [{ sign: "†", text: m.night_cycle_redrawn() }]
      : []),
  ]);
  const live = $derived(current == null ? null : show(current));
  const ahead = $derived(projected == null ? null : show(projected.mid));
</script>

{#if axis != null && marks != null && past.length > 0}
  <section
    class={cn("@container grid gap-5", className)}
    aria-labelledby="{id}-title"
  >
    <h2 id="{id}-title" class="text-[0.8125rem] font-semibold">
      {m.night_cycle_title({ year })}
    </h2>

    <dl class="grid grid-cols-2 gap-5 @2xl:grid-cols-4">
      {#each past as entry (entry.line.kind)}
        {const shown = $derived(show(entry.value))}
        {const mark = $derived(sign(entry.line))}
        <div
          class="grid content-start gap-1.5 border-s-[3px] ps-3"
          style:border-color={shown.color}
        >
          <dt class="text-xs text-muted-foreground">
            {#if entry.line.slug != null}
              <a
                href={resolve("/[...path]", { path: entry.line.slug })}
                class="underline decoration-border decoration-dotted underline-offset-3 transition-colors hover:text-foreground hover:decoration-foreground"
                >{m.night_cycle_line({ office: entry.line.label, year })}</a
              >
            {:else}
              {m.night_cycle_line({ office: entry.line.label, year })}
            {/if}
            {#if mark != null}<sup>{mark}</sup>{/if}
          </dt>
          <dd
            class="font-figures text-[2rem]/none font-[780] tracking-tight tabular-nums @md:text-[2.75rem]"
            style:color={shown.color}
          >
            {shown.text}
          </dd>
        </div>
      {/each}

      <div
        class="grid content-start gap-1.5 border-s-[3px] ps-3"
        style:border-color={live?.color ?? "var(--border)"}
      >
        <dt class="text-xs text-muted-foreground">{m.night_cycle_now()}</dt>
        <dd
          class="font-figures text-[2rem]/none font-[780] tracking-tight tabular-nums @md:text-[2.75rem]"
          style:color={live?.color ?? "var(--muted-foreground)"}
        >
          {live?.text ?? "–"}
        </dd>
        <dd class="text-xs text-muted-foreground">
          {#if total === 0}
            {m.night_cycle_now_none()}
          {:else if counted != null}
            {m.night_cycle_now_counted({ share: percent(counted, 0) })}
          {/if}
        </dd>
        {#if current != null}
          {const moved = $derived(shift(current))}
          {#if moved != null}
            <dd class="text-xs font-semibold" style:color={moved.color}>
              {moved.text}
            </dd>
          {/if}
        {/if}
      </div>

      {#if projected != null && ahead != null}
        {const moved = $derived(shift(projected.mid))}
        <div
          class="grid content-start gap-1.5 border-s-[3px] border-dashed ps-3"
          style:border-color={ahead.color}
        >
          <dt class="text-xs text-muted-foreground">
            {m.night_cycle_projected()}
          </dt>
          <dd
            class="font-figures text-[2rem]/none font-[780] tracking-tight tabular-nums @md:text-[2.75rem]"
            style:color={ahead.color}
          >
            {ahead.text}
          </dd>
          <dd class="text-xs text-muted-foreground">
            {m.night_cycle_range({
              low: show(projected.low).text,
              high: show(projected.high).text,
            })}
          </dd>
          {#if moved != null}
            <dd class="text-xs font-semibold" style:color={moved.color}>
              {moved.text}
            </dd>
          {/if}
        </div>
      {/if}
    </dl>

    <div
      class="grid gap-1"
      role="img"
      aria-label={m.night_cycle_axis({
        left: show(-scale).text,
        right: show(scale).text,
      })}
    >
      <div class="relative block-8" aria-hidden="true">
        {#each past as entry (entry.line.kind)}
          <span
            class={cn(
              "absolute -translate-x-1/2 text-[0.6875rem]/4 font-semibold whitespace-nowrap text-muted-foreground rtl:translate-x-1/2",
              { "inset-be-0": entry.row === 0, "inset-bs-0": entry.row !== 0 },
            )}
            style:inset-inline-start={place(entry.value, 0.12)}
            >{entry.line.label}</span
          >
        {/each}
      </div>
      <div class="relative block-6" aria-hidden="true">
        <span
          class="absolute inset-x-0 inset-y-2.5 rounded-full"
          style:background="linear-gradient(to right, color-mix(in oklab, {tint(
            axis.left,
          )} 26%, transparent), var(--muted) 50%, color-mix(in oklab, {tint(
            axis.right,
          )} 26%, transparent))"
        ></span>
        <span class="absolute inset-y-0 inset-s-1/2 bg-foreground/50 inline-px"
        ></span>
        {#if projected != null && ahead != null}
          <span
            class="absolute inset-y-1.5 rounded-xs border border-dashed"
            style:inset-inline-start={place(projected.low)}
            style:inset-inline-end="calc(100% - {place(projected.high)})"
            style:border-color={ahead.color}
            style:background-color="color-mix(in oklab, {ahead.color} 16%, transparent)"
          ></span>
        {/if}
        {#each past as entry (entry.line.kind)}
          <span
            class="absolute inset-y-1.5 -translate-x-1/2 rounded-full border-[2.5px] bg-background inline-3 rtl:translate-x-1/2"
            style:inset-inline-start={place(entry.value)}
            style:border-color={show(entry.value).color}
          ></span>
        {/each}
        {#if current != null && live != null}
          <span
            class="absolute inset-y-0.75 -translate-x-1/2 rounded-full ring-2 ring-background transition-[inset-inline-start] duration-700 inline-4.5 motion-reduce:transition-none rtl:translate-x-1/2"
            style:inset-inline-start={place(current)}
            style:background-color={live.color}
          ></span>
        {/if}
      </div>
      <div class="relative block-4" aria-hidden="true">
        {#if current != null}
          <span
            class="absolute inset-bs-0 -translate-x-1/2 text-[0.6875rem]/4 font-semibold whitespace-nowrap rtl:translate-x-1/2"
            style:inset-inline-start={place(current, 0.08)}
            >{m.night_cycle_now()}</span
          >
        {/if}
      </div>
      <div
        class="grid grid-cols-3 text-[0.6875rem] text-muted-foreground"
        aria-hidden="true"
      >
        <span class="font-figures tabular-nums" style:color={tint(axis.left)}
          >{show(-scale).text}</span
        >
        <span class="text-center">{m.night_cycle_even()}</span>
        <span
          class="text-end font-figures tabular-nums"
          style:color={tint(axis.right)}>{show(scale).text}</span
        >
      </div>
    </div>

    {#if notes.length > 0}
      <ul class="grid gap-0.5 text-xs text-muted-foreground">
        {#each notes as note (note.sign)}
          <li><sup>{note.sign}</sup> {note.text}</li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
