<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import Measured from "#lib/components/measured.svelte";
  import { count, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  let {
    places,
    span,
    colors,
    names,
    noun,
    focus = $bindable(null),
  }: {
    places: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["places"]
    >;
    span: { from: string; to: string; forward: boolean };
    colors: Array<string>;
    names: Array<string>;
    noun: string;
    focus?: number | null;
  } = $props();

  const PAD = { top: 14, right: 14, bottom: 34, left: 40 };

  let width = $state(0);

  const dots = $derived(
    places.rows.flatMap((row) => {
      if (row.earlier == null || row.leader == null) {
        return [];
      }
      const before = span.forward ? row.shares : row.earlier;
      const after = span.forward ? row.earlier : row.shares;
      const then = (before[0] ?? 0) - (before[1] ?? 0);
      const now = (after[0] ?? 0) - (after[1] ?? 0);
      return [
        {
          k: row.k,
          name: row.name,
          path: row.path,
          valid: row.valid,
          then,
          now,
          swing: now - then,
          winner: span.forward ? (row.prior ?? row.leader) : row.leader,
        },
      ];
    }),
  );
  const reach = $derived.by(() => {
    let most = 0.1;
    for (const dot of dots) {
      most = Math.max(most, Math.abs(dot.then));
    }
    return Math.min(1, Math.ceil((most + 0.02) * 10) / 10);
  });
  const lift = $derived.by(() => {
    let most = 0.02;
    for (const dot of dots) {
      most = Math.max(most, Math.abs(dot.swing));
    }
    const step = most > 0.2 ? 0.1 : most > 0.08 ? 0.05 : 0.02;
    return Math.ceil((most * 1.08) / step) * step;
  });
  const height = $derived(
    Math.round(Math.min(400, Math.max(260, width * 0.58))),
  );
  const sx = (value: number) =>
    PAD.left + ((value + reach) / (2 * reach)) * (width - PAD.left - PAD.right);
  const sy = (value: number) =>
    PAD.top + ((lift - value) / (2 * lift)) * (height - PAD.top - PAD.bottom);
  const grid = (edge: number) => {
    const step =
      edge > 0.5 ? 0.25 : edge > 0.2 ? 0.1 : edge > 0.08 ? 0.05 : 0.02;
    const found: Array<number> = [];
    for (let tick = -edge; tick <= edge + 0.0001; tick += step) {
      found.push(Math.round(tick / step) * step);
    }
    return [...new Set(found)];
  };
  const across = $derived(grid(reach));
  const up = $derived(grid(lift));
  const biggest = $derived(Math.max(1, ...dots.map((dot) => dot.valid)));
  const drawn = $derived(dots.toSorted((a, b) => b.valid - a.valid));
  const movers = $derived(
    dots.toSorted((a, b) => Math.abs(b.swing) - Math.abs(a.swing)).slice(0, 8),
  );
  const largest = $derived(
    Math.max(0.01, ...movers.map((dot) => Math.abs(dot.swing))),
  );
  const lit = $derived(
    focus == null ? null : (dots.find((dot) => dot.k === focus) ?? null),
  );

  function label(value: number) {
    if (Math.abs(value) < 0.0005) {
      return m.scatter_even();
    }
    return `${value > 0 ? (names[0] ?? "") : (names[1] ?? "")} +${Math.round(Math.abs(value) * 100)}`;
  }
</script>

{#if dots.length >= 3}
  <section
    class="relative col-span-12 grid grid-cols-12 gap-px bg-border"
    aria-labelledby="shift-title"
    data-export
  >
    <Export name="shift" class="absolute inset-e-2 inset-bs-2" />
    <div
      class="col-span-12 grid grid-cols-[minmax(0,1fr)] content-start gap-2 bg-card px-3.5 py-3 pe-8 min-inline-0 lg:col-span-7"
    >
      <div class="grid gap-0.5">
        <h2 id="shift-title" class="text-[0.8125rem] font-bold">
          {m.scatter_title({ noun })}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.scatter_lede({
            from: span.from,
            to: span.to,
            a: names[0] ?? "",
            b: names[1] ?? "",
          })}
        </p>
      </div>
      <Measured
        bind:width
        class="relative min-inline-0"
        reserve={"clamp(260px, 58vw, 400px)"}
      >
        <svg
          class="block overflow-visible block-auto max-inline-full"
          viewBox="0 0 {width} {height}"
          {width}
          {height}
          role="img"
          aria-label={m.scatter_label({ from: span.from, to: span.to, noun })}
        >
          <rect
            x={sx(-reach)}
            y={sy(lift)}
            width={sx(reach) - sx(-reach)}
            height={sy(0) - sy(lift)}
            fill="color-mix(in oklab, {colors[0] ??
              'var(--muted-foreground)'} 7%, transparent)"
          />
          <rect
            x={sx(-reach)}
            y={sy(0)}
            width={sx(reach) - sx(-reach)}
            height={sy(-lift) - sy(0)}
            fill="color-mix(in oklab, {colors[1] ??
              'var(--muted-foreground)'} 7%, transparent)"
          />
          {#each across as tick (tick)}
            <line
              x1={sx(tick)}
              x2={sx(tick)}
              y1={sy(-lift)}
              y2={sy(lift)}
              class={cn("stroke-border", {
                "stroke-muted-foreground/60": tick === 0,
              })}
            />
            <text
              x={sx(tick)}
              y={sy(-lift) + 15}
              opacity={tick === across[0] ? 0 : 1}
              text-anchor="middle"
              class="font-figures text-[0.625rem] tabular-nums"
              fill={tick === 0
                ? "var(--muted-foreground)"
                : colors[tick > 0 ? 0 : 1]}
              >{Math.round(Math.abs(tick) * 100)}</text
            >
          {/each}
          {#each up as tick (tick)}
            <line
              x1={sx(-reach)}
              x2={sx(reach)}
              y1={sy(tick)}
              y2={sy(tick)}
              class={cn("stroke-border", {
                "stroke-foreground/50": tick === 0,
              })}
            />
            <text
              x={sx(-reach) - 6}
              y={sy(tick) + 3.5}
              text-anchor="end"
              class="font-figures text-[0.625rem] tabular-nums"
              fill={tick === 0
                ? "var(--muted-foreground)"
                : colors[tick > 0 ? 0 : 1]}
              >{Math.round(Math.abs(tick) * 100)}</text
            >
          {/each}
          <text
            x={sx(-reach) + 6}
            y={sy(lift) + 14}
            class="text-[0.6875rem] font-semibold"
            fill={colors[0]}>{m.scatter_toward({ name: names[0] ?? "" })}</text
          >
          <text
            x={sx(-reach) + 6}
            y={sy(-lift) - 7}
            class="text-[0.6875rem] font-semibold"
            fill={colors[1]}>{m.scatter_toward({ name: names[1] ?? "" })}</text
          >
          <text
            x={sx(0)}
            y={height - 4}
            text-anchor="middle"
            class="fill-foreground text-[0.6875rem] font-semibold"
            >{m.scatter_axis({ year: span.from })}</text
          >
          <text
            transform="translate(10 {sy(0)}) rotate(-90)"
            text-anchor="middle"
            class="fill-foreground text-[0.6875rem] font-semibold"
            >{m.scatter_change()}</text
          >
          {#each drawn as dot (dot.k)}
            <circle
              cx={sx(dot.then)}
              cy={sy(dot.swing)}
              r={2 + Math.sqrt(dot.valid / biggest) * 14}
              fill={colors[dot.winner] ?? "var(--muted-foreground)"}
              fill-opacity={focus == null || focus === dot.k ? 0.72 : 0.25}
              class={cn("stroke-card", {
                "stroke-foreground":
                  focus === dot.k || Math.sign(dot.then) !== Math.sign(dot.now),
              })}
              stroke-width={focus === dot.k
                ? 2
                : Math.sign(dot.then) !== Math.sign(dot.now)
                  ? 1.25
                  : 0.75}
              role="presentation"
              onpointerenter={() => (focus = dot.k)}
              onpointerleave={() => (focus = null)}
            />
          {/each}
        </svg>
        {#if lit != null}
          <div
            class="pointer-events-none absolute grid gap-0.5 rounded-sm bg-card/95 px-2.5 py-1.5 text-xs shadow-menu ring-1 ring-border"
            style:inset-inline-start="{Math.min(
              width - 180,
              sx(lit.then) + 12,
            )}px"
            style:inset-block-start="{Math.max(0, sy(lit.swing) - 70)}px"
          >
            <span class="text-[0.8125rem] font-semibold">{lit.name}</span>
            <span class="font-figures text-muted-foreground tabular-nums"
              >{span.from}: {label(lit.then)}</span
            >
            <span class="font-figures tabular-nums"
              >{span.to}: {label(lit.now)}</span
            >
            <span
              class="font-figures font-semibold tabular-nums"
              style:color={colors[lit.swing >= 0 ? 0 : 1]}
              >{m.scatter_toward({ name: names[lit.swing >= 0 ? 0 : 1] ?? "" })}
              {points(Math.abs(lit.swing))}</span
            >
          </div>
        {/if}
      </Measured>
    </div>
    <div
      class="col-span-12 grid content-start gap-2 bg-card px-3.5 py-3 min-inline-0 lg:col-span-5"
    >
      <h3 class="text-[0.8125rem] font-bold">{m.scatter_movers()}</h3>
      <ol class="grid">
        {#each movers as dot (dot.k)}
          <li>
            <svelte:element
              this={dot.path == null ? "div" : "a"}
              href={dot.path == null
                ? undefined
                : resolve("/[...path]", { path: dot.path })}
              class={cn(
                "grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-3 rounded-xs px-1.5 py-1 text-[0.8125rem]",
                { "bg-secondary/70": focus === dot.k },
                { "hover:bg-secondary": dot.path != null },
              )}
              role={dot.path == null ? "presentation" : undefined}
              onpointerenter={() => (focus = dot.k)}
              onpointerleave={() => (focus = null)}
              onfocus={() => (focus = dot.k)}
              onblur={() => (focus = null)}
            >
              <span class="grid min-inline-0">
                <span class="truncate font-semibold">{dot.name}</span>
                <span
                  class="font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                  >{count(dot.valid)}</span
                >
              </span>
              <span class="grid grid-cols-2 items-center block-3">
                <span class="flex justify-end">
                  {#if dot.swing < 0}
                    <span
                      class="block rounded-s-xs block-3"
                      style:inline-size="{(Math.abs(dot.swing) / largest) *
                        100}%"
                      style:background-color={colors[1]}
                    ></span>
                  {/if}
                </span>
                <span class="flex border-s border-muted-foreground/60">
                  {#if dot.swing > 0}
                    <span
                      class="block rounded-e-xs block-3"
                      style:inline-size="{(dot.swing / largest) * 100}%"
                      style:background-color={colors[0]}
                    ></span>
                  {/if}
                </span>
              </span>
              <span
                class="text-end font-figures text-xs tabular-nums inline-10"
                style:color={colors[dot.swing >= 0 ? 0 : 1]}
                >{points(Math.abs(dot.swing))}</span
              >
            </svelte:element>
          </li>
        {/each}
      </ol>
      <p class="text-xs text-muted-foreground">
        {m.scatter_movers_note({ from: span.from, to: span.to })}
      </p>
    </div>
  </section>
{/if}
