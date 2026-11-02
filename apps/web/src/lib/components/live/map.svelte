<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import type { ComponentProps } from "svelte";
  import MapIcon from "@lucide/svelte/icons/map";
  import XIcon from "@lucide/svelte/icons/x";
  import { untrack } from "svelte";
  import type {
    Geography,
    Mode,
    Standing,
    UnitStanding,
  } from "#lib/live/standing.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count, percent } from "#lib/format.js";
  import { compact, lead, placeName, surname, tone } from "#lib/live/format.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import { remainder } from "#lib/live/standing.js";
  import { basemap, SCHEMES } from "#lib/map/basemap.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import MethodLink from "./method-link.svelte";

  let {
    geography,
    options,
    now,
    estimate,
    modes = ["lead", "bubbles", "shift", "counted", "remaining"],
    title,
    caption,
    help,
    anchor = null,
    href,
    mode = $bindable("lead"),
    selected = $bindable(null),
    hovered = $bindable(null),
  }: {
    geography: Geography | null;
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    modes?: Array<Mode>;
    title?: string;
    caption?: string;
    help?: ComponentProps<typeof MethodLink>["section"];
    anchor?: string | null;
    href?: (unitId: string) => string | null;
    mode?: Mode;
    selected?: string | null;
    hovered?: string | null;
  } = $props();

  const LABELS: Array<{ id: Mode; label: () => string }> = [
    { id: "lead", label: m.night_map_lead },
    { id: "bubbles", label: m.night_map_bubbles },
    { id: "shift", label: m.night_map_shift },
    { id: "counted", label: m.night_map_counted },
    { id: "remaining", label: m.night_map_remaining },
  ];
  const choices = $derived(
    LABELS.flatMap((entry) =>
      modes.includes(entry.id)
        ? [{ value: entry.id, label: entry.label() }]
        : [],
    ),
  );

  const byArea = $derived(
    new Map(
      [...now.units.values()].flatMap((row) =>
        row.unit.areaId == null ? [] : [[row.unit.areaId, row] as const],
      ),
    ),
  );
  const places = $derived(geography?.places ?? []);
  const kOf = $derived(new Map(places.map((place) => [place.areaId, place.k])));
  const unitOfK = $derived(
    new Map(
      places.flatMap((place) => {
        const row = byArea.get(place.areaId);
        return row == null ? [] : [[place.k, row.unit.id] as const];
      }),
    ),
  );
  const projected = $derived(
    new Map((estimate?.units ?? []).map((unit) => [unit.unitId, unit])),
  );
  const a = $derived(now.pair[0]);
  const b = $derived(now.pair[1]);
  const toneA = $derived(tone(options[a]));
  const toneB = $derived(tone(options[b]));
  const largest = $derived(
    Math.max(
      1,
      ...[...now.units.values()].map(
        (row) => row.baseline?.turnout ?? row.total,
      ),
    ),
  );

  let pointer = $state<{ x: number; y: number } | null>(null);
  let clicked = $state<string | null>(null);
  let frameWidth = $state(0);
  let frameHeight = $state(0);
  let tipWidth = $state(220);
  let tipHeight = $state(140);
  let painted = "";

  const engine = new Engine(
    untrack(() => geography?.frame) ?? {
      west: -90,
      south: 41,
      east: -82,
      north: 48,
    },
    (width, height) => {
      const edge = Math.max(10, Math.round(Math.min(width, height) * 0.04));
      return { top: edge, right: edge, bottom: edge, left: edge };
    },
    {
      onReady: () => {
        const gl = engine.gl;
        if (gl != null) {
          gl.scrollZoom.disable();
          gl.dragPan.disable();
          gl.doubleClickZoom.disable();
          gl.boxZoom.disable();
          gl.touchZoomRotate.disable();
        }
        untrack(draw);
      },
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hovered = null;
          pointer = null;
          engine.cursor(false);
          return;
        }
        const k = engine.pick(point, ["live-fill"]);
        const unit = k == null ? null : (unitOfK.get(k) ?? null);
        if (kind === "click") {
          selected = unit === selected ? null : unit;
          clicked = selected;
          return;
        }
        if (isTouch) {
          return;
        }
        hovered = unit;
        pointer = unit == null ? null : point;
        engine.cursor(unit != null);
      },
    },
  );

  function expectedOf(row: UnitStanding) {
    return (
      projected.get(row.unit.id)?.expectedTotal.mid ??
      row.baseline?.turnout ??
      row.total
    );
  }

  function fill(row: UnitStanding): string | null {
    const muted =
      "color-mix(in oklab, var(--muted-foreground) 14%, var(--background))";
    switch (mode) {
      case "lead": {
        if (row.leader == null) {
          return muted;
        }
        const strength = Math.round(26 + 74 * Math.min(row.margin / 0.4, 1));
        return `color-mix(in oklab, ${tone(options[row.leader])} ${strength}%, var(--background))`;
      }
      case "shift": {
        if (row.signed == null || row.baseSigned == null) {
          return muted;
        }
        const shift = row.signed - row.baseSigned;
        const strength = Math.round(
          12 + 88 * Math.min(Math.abs(shift) / 0.15, 1),
        );
        return `color-mix(in oklab, ${shift >= 0 ? toneA : toneB} ${strength}%, var(--background))`;
      }
      case "counted": {
        const share = Math.min(1, row.total / Math.max(1, expectedOf(row)));
        return row.total === 0
          ? muted
          : `color-mix(in oklab, var(--foreground) ${Math.round(8 + 80 * share)}%, var(--background))`;
      }
      default: {
        return row.total > 0
          ? "color-mix(in oklab, var(--muted-foreground) 16%, var(--background))"
          : muted;
      }
    }
  }

  const circles = $derived.by(() => {
    if (mode !== "bubbles" && mode !== "remaining") {
      return [];
    }
    return places.flatMap((place) => {
      const row = byArea.get(place.areaId);
      if (row == null) {
        return [];
      }
      if (mode === "bubbles") {
        if (row.leader == null || row.lead <= 0) {
          return [];
        }
        return [
          {
            k: place.k,
            at: place.at,
            value: row.lead,
            color: tone(options[row.leader]),
            reference: largest * 0.35,
          },
        ];
      }
      const rest = remainder(row, estimate, options, now.pair);
      if (rest.left < 1) {
        return [];
      }
      const lean = rest.lean ?? 0;
      const strength = Math.round(40 + 55 * Math.min(Math.abs(lean) / 0.3, 1));
      return [
        {
          k: place.k,
          at: place.at,
          value: rest.left,
          color: `color-mix(in oklab, ${lean >= 0 ? toneA : toneB} ${strength}%, var(--background))`,
          reference: largest * 0.8,
        },
      ];
    });
  });

  function draw() {
    if (geography == null || engine.gl == null) {
      return;
    }
    const source = engine.tiles(geography.tiles, "live");
    if (painted !== geography.tiles) {
      engine.drop("live-layers");
      const ks = places.map((place) => place.k);
      engine.add(
        {
          id: "live-fill",
          type: "fill",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "fill-color": [
              "coalesce",
              ["feature-state", "color"],
              "color-mix(in oklab, var(--muted-foreground) 14%, var(--background))",
            ],
            "fill-color-transition": { duration: 600 },
          },
        },
        "fills",
        { group: "live-layers", rank: 0 },
      );
      engine.add(
        {
          id: "live-seam",
          type: "line",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "line-color": "var(--background)",
            "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 9, 1.4],
          },
        },
        "lines",
        { group: "live-layers" },
      );
      engine.add(
        {
          id: "live-focus",
          type: "line",
          source,
          "source-layer": "areas",
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "line-color": "var(--foreground)",
            "line-width": [
              "case",
              ["boolean", ["feature-state", "pinned"], false],
              2.4,
              ["boolean", ["feature-state", "anchor"], false],
              2.4,
              ["boolean", ["feature-state", "hover"], false],
              1.6,
              0,
            ],
          },
        },
        "focus",
        { group: "live-layers" },
      );
      if (geography.outline.length > 0) {
        engine.add(
          {
            id: "live-veil",
            type: "fill",
            source: engine.veil("live-veil", geography.outline, "live-layers"),
            paint: {
              "fill-color": "var(--background)",
              "fill-opacity": 0.55,
              "fill-antialias": false,
            },
          },
          "etch",
          { group: "live-layers", rank: 1 },
        );
      }
      engine.frame(geography.frame);
      painted = geography.tiles;
    }
    engine.paint(
      source,
      new Map(
        places.flatMap((place) => {
          const row = byArea.get(place.areaId);
          const color = row == null ? null : fill(row);
          return color == null ? [] : [[place.k, { color }] as const];
        }),
      ),
    );
    const scale = Math.max(...circles.map((circle) => circle.reference), 1);
    const dots = engine.points(
      "live-dots",
      circles.map((circle) => ({
        k: circle.k,
        at: circle.at,
        properties: {
          r: Math.max(2.5, 30 * Math.sqrt(circle.value / scale)),
          c: engine.read(circle.color),
        },
      })),
      "live-layers",
    );
    if (!engine.has("live-dots")) {
      engine.add(
        {
          id: "live-dots",
          type: "circle",
          source: dots,
          paint: {
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              4,
              ["*", ["get", "r"], 0.55],
              6,
              ["*", ["get", "r"], 0.9],
              9,
              ["*", ["get", "r"], 1.8],
            ],
            "circle-color": ["get", "c"],
            "circle-opacity": 0.82,
            "circle-stroke-color": "var(--background)",
            "circle-stroke-width": 1,
          },
        },
        "points",
        { group: "live-layers" },
      );
    }
    const hoverK =
      hovered == null
        ? null
        : (kOf.get(now.units.get(hovered)?.unit.areaId ?? "") ?? null);
    const pinnedK =
      selected == null
        ? null
        : (kOf.get(now.units.get(selected)?.unit.areaId ?? "") ?? null);
    engine.mark("hover", source, hoverK);
    engine.mark("pinned", source, pinnedK);
    engine.mark(
      "anchor",
      source,
      anchor == null ? null : (kOf.get(anchor) ?? null),
    );
  }

  $effect(() => {
    void [
      now,
      mode,
      estimate,
      hovered,
      selected,
      anchor,
      engine.theme,
      circles,
    ];
    if (engine.isLoaded && geography != null) {
      untrack(draw);
    }
  });

  $effect(() => {
    if (engine.gl == null || geography == null) {
      return;
    }
    const scheme = geography.scheme;
    const outline = geography.outline;
    const isDetailed = prefs.isBasemap;
    untrack(() => basemap(engine, scheme, isDetailed, outline));
  });

  const pinned = $derived(
    selected == null || selected !== clicked
      ? null
      : (now.units.get(selected) ?? null),
  );
  const focus = $derived(
    hovered == null || (hovered === selected && pinned != null)
      ? null
      : (now.units.get(hovered) ?? null),
  );
  const isLive = $derived(estimate?.status === "estimating" && !now.isFinal);
  const hasBasemap = $derived(
    geography != null && SCHEMES.has(geography.scheme),
  );
  const tipLeft = $derived(
    pointer == null
      ? 0
      : pointer.x + 16 + tipWidth > frameWidth
        ? Math.max(0, pointer.x - 16 - tipWidth)
        : pointer.x + 16,
  );
  const tipTop = $derived(
    pointer == null
      ? 0
      : pointer.y + 16 + tipHeight > frameHeight
        ? Math.max(-tipHeight / 2, pointer.y - 16 - tipHeight)
        : pointer.y + 16,
  );
  const signedText = (value: number) =>
    `${value >= 0 ? surname(options[a]) : surname(options[b])} +${lead(value)}`;
  const legend = $derived.by(() => {
    switch (mode) {
      case "lead":
        return m.night_legend_lead();
      case "bubbles":
        return m.night_legend_bubbles();
      case "shift":
        return m.night_legend_shift();
      case "counted":
        return m.night_legend_counted();
      default:
        return m.night_legend_remaining();
    }
  });
</script>

<svelte:window
  onkeydown={(event: KeyboardEvent) => {
    if (event.key === "Escape" && selected != null) {
      selected = null;
    }
  }}
/>

<figure class="grid content-start gap-2.5" aria-busy={geography == null}>
  <div
    class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 min-block-8"
  >
    {#if title != null}
      <h2 class="flex items-center gap-1 text-base font-[720] tracking-tight">
        {title}
        {#if help != null}
          <MethodLink section={help} />
        {/if}
      </h2>
    {/if}
    {#if choices.length > 1}
      <Segmented
        options={choices}
        bind:value={mode}
        label={m.night_map_modes()}
      />
    {:else if mode === "remaining"}
      <span class="flex items-center gap-3 text-xs text-muted-foreground">
        <span class="flex items-center gap-1.5"
          ><span
            class="rounded-full block-2 inline-2"
            style:background-color={toneA}
          ></span>{surname(options[a])}</span
        >
        <span class="flex items-center gap-1.5"
          ><span
            class="rounded-full block-2 inline-2"
            style:background-color={toneB}
          ></span>{surname(options[b])}</span
        >
      </span>
    {/if}
  </div>
  <div
    class="relative"
    bind:clientWidth={frameWidth}
    bind:clientHeight={frameHeight}
  >
    <div
      class={cn(
        "relative aspect-[1/1.08] overflow-hidden rounded-sm bg-background ring-1 ring-border inline-full sm:aspect-auto sm:block-[clamp(20rem,calc(100svh-29rem),34rem)]",
        {
          "animate-pulse bg-muted motion-reduce:animate-none":
            geography == null || !engine.isLoaded,
        },
      )}
    >
      <div
        class={cn(
          "transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none [&_.maplibregl-canvas-container]:cursor-default!",
          { "opacity-0": !engine.isLoaded },
        )}
        {@attach engine.mount}
      ></div>
      {#if geography == null}
        <p
          class="absolute inset-0 grid place-items-center text-sm text-muted-foreground"
        >
          {m.night_map_none()}
        </p>
      {/if}
      {#if hasBasemap}
        <button
          type="button"
          class={cn(
            "absolute inset-e-2 inset-be-2 z-20 grid place-items-center rounded-sm bg-background/90 shadow-xs ring-1 ring-border backdrop-blur-sm block-8 inline-8 hover:bg-secondary pointer-coarse:block-10 pointer-coarse:inline-10",
            { "text-muted-foreground": !prefs.isBasemap },
          )}
          aria-label={m.map_basemap()}
          aria-pressed={prefs.isBasemap}
          title={m.map_basemap()}
          onclick={() => (prefs.isBasemap = !prefs.isBasemap)}
        >
          <MapIcon class="block-4 inline-4" />
        </button>
      {/if}
    </div>
    {#if pinned != null}
      {const link = $derived(href?.(pinned.unit.id) ?? null)}
      <div
        class="z-20 mbs-2 grid gap-1.5 rounded-sm bg-popover px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border sm:absolute sm:inset-e-2 sm:inset-bs-2 sm:mbs-0 sm:inline-64"
        role="region"
        aria-label={pinned.unit.name}
      >
        <div class="flex items-start justify-between gap-2">
          <span class="font-semibold">{placeName(pinned.unit.name)}</span>
          <button
            type="button"
            class="-me-1.5 -mbs-1 grid shrink-0 place-items-center rounded-xs text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
            aria-label={m.night_map_close()}
            title={m.night_map_close()}
            onclick={() => (selected = null)}
          >
            <XIcon class="block-3.5 inline-3.5" />
          </button>
        </div>
        {@render detail(pinned, 6)}
        {#if link != null}
          <a
            href={link}
            class="border-bs border-border pbs-1.5 text-xs font-semibold underline-offset-2 hover:underline"
            >{m.night_map_open({ place: placeName(pinned.unit.name) })}</a
          >
        {/if}
      </div>
    {/if}
    {#if pointer != null && focus != null}
      <div
        class="pointer-events-none absolute z-30 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-56"
        style:inset-inline-start="{tipLeft}px"
        style:inset-block-start="{tipTop}px"
        bind:offsetWidth={tipWidth}
        bind:offsetHeight={tipHeight}
      >
        <span class="font-semibold">{placeName(focus.unit.name)}</span>
        {@render detail(focus, 3)}
      </div>
    {/if}
  </div>
  <p
    class="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground"
  >
    <span>{caption ?? legend}</span>
    {#if hasBasemap && prefs.isBasemap}
      <span class="text-[0.6875rem]">{m.map_basemap_credit()}</span>
    {/if}
  </p>
</figure>

{#snippet detail(row: UnitStanding, limit: number)}
  {const total = $derived(row.total)}
  {const rest = $derived(remainder(row, estimate, options, now.pair))}
  {#if total > 0}
    {#each row.order.slice(0, limit) as index (index)}
      {const share = $derived((row.votes[index] ?? 0) / total)}
      <span class="grid gap-0.5">
        <span class="flex items-baseline gap-2 font-figures tabular-nums">
          <span
            class="shrink-0 self-center rounded-full block-2 inline-2"
            style:background-color={tone(options[index])}
          ></span>
          <span
            class={cn("grow truncate font-sans min-inline-0", {
              "font-semibold": index === row.leader,
            })}>{surname(options[index])}</span
          >
          <span class="text-muted-foreground"
            >{count(row.votes[index] ?? 0)}</span
          >
          <span class="text-end font-semibold min-inline-11"
            >{percent(share)}</span
          >
        </span>
        {#if limit > 3}
          <span
            class="mis-4 relative overflow-hidden rounded-xs bg-muted block-1"
            aria-hidden="true"
          >
            <span
              class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-500 motion-reduce:transition-none"
              style:inline-size="{share * 100}%"
              style:background-color={tone(options[index])}
            ></span>
          </span>
        {/if}
      </span>
    {/each}
    <span class="text-xs text-muted-foreground">
      {m.night_tip_counted({
        share: percent(Math.min(1, total / Math.max(1, rest.expected)), 0),
      })}
    </span>
  {:else}
    <span class="text-xs text-muted-foreground">{m.night_tip_waiting()}</span>
  {/if}
  {#if isLive && rest.left >= 1}
    <span
      class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 border-bs border-border pbs-1.5 text-xs"
    >
      <span class="text-muted-foreground">{m.night_table_left()}</span>
      <span class="text-end font-figures font-semibold tabular-nums"
        >{compact(rest.left)}</span
      >
      {#if rest.lean != null}
        <span class="text-muted-foreground">{m.night_table_lean()}</span>
        <span
          class="text-end font-figures font-semibold tabular-nums"
          style:color={rest.lean >= 0 ? toneA : toneB}
          >{signedText(rest.lean)}</span
        >
      {/if}
    </span>
  {/if}
  {#if row.baseSigned != null}
    <span class="text-xs text-muted-foreground">
      {m.night_tip_baseline({ margin: signedText(row.baseSigned) })}
    </span>
  {/if}
{/snippet}
