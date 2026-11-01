<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import { untrack } from "svelte";
  import type {
    Geography,
    Mode,
    Standing,
    UnitStanding,
  } from "#lib/live/standing.js";
  import { count, percent } from "#lib/format.js";
  import { compact, lead, surname, tone } from "#lib/live/format.js";
  import { remainder } from "#lib/live/standing.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    geography,
    options,
    now,
    estimate,
    modes = ["lead", "bubbles", "shift", "counted", "remaining"],
    title,
    caption,
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
  const choices = $derived(LABELS.filter((entry) => modes.includes(entry.id)));

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
      "color-mix(in oklab, var(--muted-foreground) 9%, var(--background))";
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
              "color-mix(in oklab, var(--muted-foreground) 9%, var(--background))",
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
              ["boolean", ["feature-state", "hover"], false],
              1.6,
              0,
            ],
          },
        },
        "focus",
        { group: "live-layers" },
      );
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
  }

  $effect(() => {
    void [now, mode, estimate, hovered, selected, engine.theme, circles];
    if (engine.isLoaded && geography != null) {
      untrack(draw);
    }
  });

  const focus = $derived(
    hovered == null ? null : (now.units.get(hovered) ?? null),
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

<figure class="grid content-start gap-2.5" aria-busy={geography == null}>
  <div
    class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 min-block-8"
  >
    {#if title != null}
      <h2 class="text-base font-[720] tracking-tight">{title}</h2>
    {/if}
    {#if choices.length > 1}
      <div
        role="radiogroup"
        aria-label={m.night_map_modes()}
        class="flex gap-0.5 rounded-sm bg-muted p-0.5"
      >
        {#each choices as entry (entry.id)}
          <button
            type="button"
            role="radio"
            aria-checked={mode === entry.id}
            class={cn(
              "rounded-xs px-2.5 py-1 text-[0.8125rem] font-semibold transition-colors",
              {
                "bg-background text-foreground shadow-xs": mode === entry.id,
                "text-muted-foreground hover:text-foreground":
                  mode !== entry.id,
              },
            )}
            onclick={() => (mode = entry.id)}>{entry.label()}</button
          >
        {/each}
      </div>
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
    </div>
    {#if pointer != null && focus != null}
      {const total = $derived(focus.total)}
      {const rest = $derived(remainder(focus, estimate, options, now.pair))}
      {const isLive = $derived(
        estimate?.status === "estimating" && !now.isFinal,
      )}
      <div
        class="pointer-events-none absolute z-30 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-56"
        style:inset-inline-start="{tipLeft}px"
        style:inset-block-start="{tipTop}px"
        bind:offsetWidth={tipWidth}
        bind:offsetHeight={tipHeight}
      >
        <span class="font-semibold">{focus.unit.name}</span>
        {#if total > 0}
          {#each focus.order.slice(0, 3) as index (index)}
            <span class="flex items-baseline gap-2 font-figures tabular-nums">
              <span
                class="shrink-0 self-center rounded-full block-2 inline-2"
                style:background-color={tone(options[index])}
              ></span>
              <span class="grow truncate font-sans min-inline-0"
                >{surname(options[index])}</span
              >
              <span class="text-muted-foreground"
                >{count(focus.votes[index] ?? 0)}</span
              >
              <span class="text-end font-semibold min-inline-11"
                >{percent((focus.votes[index] ?? 0) / total)}</span
              >
            </span>
          {/each}
          <span class="text-xs text-muted-foreground">
            {m.night_tip_counted({
              share: percent(
                Math.min(1, total / Math.max(1, rest.expected)),
                0,
              ),
            })}
          </span>
        {:else}
          <span class="text-xs text-muted-foreground"
            >{m.night_tip_waiting()}</span
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
        {#if focus.baseSigned != null}
          <span class="text-xs text-muted-foreground">
            {m.night_tip_baseline({ margin: signedText(focus.baseSigned) })}
          </span>
        {/if}
      </div>
    {/if}
  </div>
  <p class="text-xs text-muted-foreground">{caption ?? legend}</p>
</figure>
