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
  import { Switch } from "#lib/components/ui/switch/index.js";
  import { count, percent } from "#lib/format.js";
  import { compact, lead, placeName, surname, tone } from "#lib/live/format.js";
  import { prefs } from "#lib/live/prefs.svelte.js";
  import { features, remainder, sum, whole } from "#lib/live/standing.js";
  import { basemap, SCHEMES } from "#lib/map/basemap.js";
  import { Engine, reach } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    bands,
    MARGIN,
    marginSafety,
    marginShade,
    safety,
    SWING,
    swingSafety,
  } from "#lib/shade.js";
  import { cn } from "#lib/utils.js";
  import FlipKey from "./flip-key.svelte";
  import Legend from "./full/legend.svelte";
  import {
    BLANK,
    GROUND,
    isRightward,
    swingOf,
    unitHatch,
  } from "./full/model.js";
  import Scale from "./full/scale.svelte";
  import LaterKey from "./later-key.svelte";
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
    answers = null,
    isLater = false,
    isAlone = false,
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
    answers?: Array<number> | null;
    isLater?: boolean;
    isAlone?: boolean;
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

  let isArrows = $state(true);
  const swings = $derived.by(() => {
    if (mode !== "shift" || !isArrows || isLater || geography?.pieces != null) {
      return [];
    }
    return places.flatMap((place) => {
      const row = byArea.get(place.areaId);
      const swing =
        row == null ? null : swingOf(place.areaId, row, options, now.pair);
      return row == null || swing == null
        ? []
        : [{ ...swing, at: place.at, unitId: row.unit.id }];
    });
  });
  const swingReach = $derived(reach(swings.map((entry) => entry.swing)));
  const arrowed = $derived(new Set(swings.map((entry) => entry.areaId)));
  const arrowSides = $derived.by(() => {
    const sides = [
      { name: surname(options[a]), color: toneA },
      { name: surname(options[b]), color: toneB },
    ];
    return isRightward(options, now.pair) ? sides.toReversed() : sides;
  });

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

  function fill(row: UnitStanding, expected = expectedOf(row)): string | null {
    const muted =
      "color-mix(in oklab, var(--muted-foreground) 14%, var(--background))";
    switch (mode) {
      case "lead": {
        if (row.leader == null) {
          return muted;
        }
        return (
          unitHatch(row, options)?.fill ??
          marginShade(tone(options[row.leader]), marginSafety(row.margin))
        );
      }
      case "shift": {
        if (row.signed == null || row.baseSigned == null) {
          return muted;
        }
        if (arrowed.has(row.unit.areaId ?? "")) {
          return GROUND;
        }
        const shift = row.signed - row.baseSigned;
        return marginShade(shift >= 0 ? toneA : toneB, swingSafety(shift));
      }
      case "counted": {
        const share = Math.min(1, row.total / Math.max(1, expected));
        return row.total === 0
          ? muted
          : marginShade("var(--map-ink)", safety(0.08 + 0.8 * share));
      }
      default: {
        return row.total > 0
          ? "color-mix(in oklab, var(--muted-foreground) 16%, var(--background))"
          : muted;
      }
    }
  }

  function stripeOf(row: UnitStanding | null) {
    return mode === "lead" && row != null
      ? (unitHatch(row, options)?.stripe ?? null)
      : null;
  }

  const hasFlips = $derived(
    (geography?.pieces == null ? [...byArea.values()] : [whole(now)]).some(
      (row) => stripeOf(row) != null,
    ),
  );

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
      return [
        {
          k: place.k,
          at: place.at,
          value: rest.left,
          color: marginShade(
            lean >= 0 ? toneA : toneB,
            safety(0.4 + 0.55 * Math.min(Math.abs(lean) / 0.3, 1)),
          ),
          reference: largest * 0.8,
        },
      ];
    });
  });

  function draw() {
    if (geography == null || engine.gl == null) {
      return;
    }
    const cut = geography.pieces;
    const key = cut == null ? geography.tiles : JSON.stringify(geography.frame);
    const source =
      cut == null ? engine.tiles(geography.tiles, "live") : "live-pieces";
    if (painted !== key) {
      engine.drop("live-layers");
      if (cut != null) {
        engine.shapes(source, features(cut), "live-layers");
      }
      const areas =
        cut == null ? { source, "source-layer": "areas" } : { source };
      const ks = places.map((place) => place.k);
      const edge =
        cut?.edge ?? (geography.isEdged === true ? geography.outline : []);
      engine.add(
        {
          id: "live-fill",
          type: "fill",
          ...areas,
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
          id: "live-later",
          type: "fill",
          ...areas,
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "fill-pattern": "absent",
            "fill-opacity": [
              "case",
              ["boolean", ["feature-state", "later"], false],
              1,
              0,
            ],
          },
        },
        "fills",
        { group: "live-layers", rank: 1 },
      );
      engine.add(
        {
          id: "live-seam",
          type: "line",
          ...areas,
          filter: ["in", ["id"], ["literal", ks]],
          paint: {
            "line-color": "var(--map-seam)",
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
          ...areas,
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
      if (edge.length > 0) {
        engine.add(
          {
            id: "live-edge",
            type: "line",
            source: engine.shapes(
              "live-edge",
              { type: "MultiLineString", coordinates: edge },
              "live-layers",
            ),
            layout: { "line-join": "round", "line-cap": "round" },
            paint: {
              "line-color":
                cut == null
                  ? "color-mix(in oklab, var(--foreground) 72%, transparent)"
                  : "var(--foreground)",
              "line-width": [
                "interpolate",
                ["linear"],
                ["zoom"],
                8,
                cut == null ? 1 : 1.4,
                13,
                cut == null ? 1.8 : 2.4,
              ],
            },
          },
          "focus",
          { group: "live-layers" },
        );
      }
      engine.frame(geography.frame);
      painted = key;
    }
    const district = cut == null ? null : whole(now);
    const shared =
      district == null
        ? null
        : fill(district, sum([...now.units.values()].map(expectedOf)));
    engine.paint(
      source,
      new Map(
        places.flatMap(
          (
            place,
          ): Array<[number, Record<string, string | number | boolean>]> => {
            const row = byArea.get(place.areaId);
            const color = row == null ? null : (shared ?? fill(row));
            if (isLater) {
              return [[place.k, { later: true }]];
            }
            const stripe = stripeOf(district ?? row ?? null);
            return color == null
              ? []
              : [
                  [
                    place.k,
                    stripe == null
                      ? { color }
                      : { color, stripe: engine.stripe("live-fill", stripe) },
                  ],
                ];
          },
        ),
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
    engine.arrows(
      swings.map((entry) => ({
        at: entry.at,
        swing: entry.swing,
        color: entry.color,
        weight: entry.weight,
        isMuted: selected != null && selected !== entry.unitId,
      })),
      swingReach,
    );
  }

  $effect(() => {
    void [
      now,
      mode,
      estimate,
      isLater,
      hovered,
      selected,
      anchor,
      engine.theme,
      circles,
      swings,
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
    const isShownAlone = isAlone;
    untrack(() =>
      basemap(engine, scheme, isDetailed, outline, false, isShownAlone),
    );
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
        return swings.length > 0
          ? m.night_legend_arrows()
          : m.night_legend_shift();
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
  {#if !isLater && (mode === "lead" || mode === "shift" || mode === "counted")}
    <div class="grid gap-2 max-inline-80">
      {#if mode === "shift" && geography?.pieces == null}
        <label
          class="flex items-center justify-between gap-3 text-xs font-semibold"
        >
          {m.map_mode_arrows()}
          <Switch
            checked={isArrows}
            onCheckedChange={(value: boolean) => (isArrows = value)}
          />
        </label>
      {/if}
      {#if mode === "counted"}
        <Scale />
      {:else if swings.length > 0}
        <Legend
          kind="arrows"
          sides={arrowSides}
          reach={swingReach}
          blank={BLANK}
        />
      {:else}
        <Scale
          sides={[
            { name: surname(options[a]), color: toneA },
            { name: surname(options[b]), color: toneB },
          ]}
          bins={bands(mode === "shift" ? SWING : MARGIN)}
        />
      {/if}
      {#if hasFlips}
        <FlipKey about={m.night_flip_unit_about()} />
      {/if}
    </div>
  {/if}
  {#if answers != null && !isLater && (mode === "bubbles" || (mode === "lead" && answers.length > 2))}
    <ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {#each answers as index (options[index]?.id ?? index)}
        <li class="flex items-center gap-1.5">
          <span
            class="shrink-0 rounded-xs block-2.5 inline-2.5"
            style:background-color={tone(options[index])}
          ></span>
          {options[index]?.label ?? ""}
        </li>
      {/each}
    </ul>
  {/if}
  <p
    class="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground"
  >
    {#if isLater}
      <LaterKey />
    {:else}
      <span>{caption ?? legend}</span>
    {/if}
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
    <span class="text-xs text-muted-foreground"
      >{isLater ? m.night_later_key() : m.night_tip_waiting()}</span
    >
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
