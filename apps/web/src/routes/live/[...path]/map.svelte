<script lang="ts">
  import { untrack } from "svelte";
  import { count, percent } from "#lib/format.js";
  import { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    contest,
    options,
    tones,
    at,
    level,
  }: {
    contest: string;
    options: Array<{ id: string; name: string }>;
    tones: Array<string>;
    at: string | null;
    level: "us.nyc_ed" | "us.nyc_ad" | "us.county";
  } = $props();

  type Sheet = Awaited<ReturnType<typeof orpc.live.map.call>>;

  let sheet = $state.raw<Sheet>(null);
  let isLoading = $state(true);
  let hover = $state<{ x: number; y: number; k: number } | null>(null);
  let painted = "";

  const NYC = { west: 16.27, south: 18.97, east: 16.63, north: 19.41 };

  const index = $derived(
    new Map(options.map((option, position) => [option.id, position])),
  );
  const rows = $derived(
    new Map(
      (sheet?.units ?? []).map((unit) => {
        const ranked = unit.votes
          .map((votes, position) => ({
            option: index.get(sheet?.options[position] ?? "") ?? -1,
            votes,
          }))
          .filter((entry) => entry.option >= 0)
          .toSorted((a, b) => b.votes - a.votes);
        let total = 0;
        for (const entry of ranked) {
          total += entry.votes;
        }
        const first = ranked[0];
        const second = ranked[1];
        return [
          unit.k,
          {
            name: unit.name,
            total,
            leader: total > 0 && first != null ? first.option : null,
            margin:
              total > 0
                ? ((first?.votes ?? 0) - (second?.votes ?? 0)) / total
                : 0,
            ranked,
          },
        ];
      }),
    ),
  );

  const engine = new Engine(
    NYC,
    (width, height) => {
      const edge = Math.max(12, Math.round(Math.min(width, height) * 0.04));
      return { top: edge, right: edge, bottom: edge, left: edge };
    },
    {
      cooperative: true,
      onReady: () => untrack(draw),
      onPointer: ({ kind, point, isTouch }) => {
        if (kind === "leave") {
          hover = null;
          engine.cursor(false);
          return;
        }
        if (kind === "move" && isTouch) {
          return;
        }
        const k = engine.pick(point, ["live-fill"]);
        hover = k != null && rows.has(k) ? { ...point, k } : null;
        engine.cursor(hover != null);
      },
    },
  );

  const leaders = $derived(
    [...new Set([...rows.values()].map((row) => row.leader))]
      .filter((leader) => leader != null)
      .toSorted((a, b) => a - b),
  );
  const focus = $derived(hover == null ? null : (rows.get(hover.k) ?? null));

  $effect(() => {
    const wanted = { contest, at, level };
    isLoading = true;
    let isCurrent = true;
    void orpc.live.map.call(wanted).then((next) => {
      if (isCurrent) {
        sheet = next;
        isLoading = false;
      }
    });
    return () => {
      isCurrent = false;
    };
  });

  $effect(() => {
    if (engine.isLoaded && sheet != null) {
      draw();
    }
  });

  function draw() {
    if (sheet == null || engine.gl == null) {
      return;
    }
    const source = engine.tiles(sheet.tiles, "live");
    if (painted !== sheet.tiles) {
      engine.drop("live-layers");
      engine.add(
        {
          id: "live-fill",
          type: "fill",
          source,
          "source-layer": "areas",
          paint: {
            "fill-color": [
              "coalesce",
              ["feature-state", "color"],
              "color-mix(in oklab, var(--muted-foreground) 10%, var(--card))",
            ],
            "fill-color-transition": { duration: 450 },
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
          paint: {
            "line-color": "var(--card)",
            "line-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              9,
              0.15,
              13,
              0.8,
            ],
          },
        },
        "lines",
        { group: "live-layers" },
      );
      engine.frame(sheet.frame);
      painted = sheet.tiles;
    }
    engine.paint(
      source,
      new Map(
        [...rows].flatMap(([k, row]) =>
          row.leader == null
            ? []
            : [
                [
                  k,
                  {
                    color: `color-mix(in oklab, ${tones[row.leader] ?? "var(--muted-foreground)"} ${Math.round(28 + 72 * Math.min(row.margin / 0.5, 1))}%, var(--card))`,
                  },
                ] as const,
              ],
        ),
      ),
    );
  }
</script>

<figure
  class={cn(
    "grid gap-2 transition-opacity duration-300 motion-reduce:transition-none",
    { "opacity-60": isLoading && sheet != null },
  )}
  aria-busy={isLoading}
>
  <div
    class={cn(
      "relative aspect-[1/1.05] overflow-hidden rounded-sm bg-card ring-1 ring-border inline-full sm:aspect-[4/3.4]",
      {
        "animate-pulse bg-muted motion-reduce:animate-none":
          (sheet == null && isLoading) || (sheet != null && !engine.isLoaded),
      },
    )}
  >
    <div
      class={cn(
        "transition-opacity duration-500 ease-out block-full inline-full motion-reduce:transition-none",
        { "opacity-0": !engine.isLoaded },
      )}
      {@attach engine.mount}
    ></div>
    {#if !isLoading && sheet == null}
      <p
        class="absolute inset-0 grid place-items-center text-sm text-muted-foreground"
      >
        {m.live_map_waiting()}
      </p>
    {/if}
    {#if hover != null && focus != null}
      <div
        class="pointer-events-none absolute z-10 grid gap-1 rounded-sm bg-card/95 px-3 py-2 text-[0.8125rem] shadow-sm ring-1 ring-border backdrop-blur-sm min-inline-44"
        style:inset-inline-start="{hover.x + 14}px"
        style:inset-block-start="{hover.y + 14}px"
      >
        <span class="font-semibold">{focus.name}</span>
        {#each focus.ranked.slice(0, 3) as entry (entry.option)}
          <span class="flex items-baseline gap-2 font-figures tabular-nums">
            <span
              class="shrink-0 rounded-full block-2 inline-2"
              style:background-color={tones[entry.option]}
            ></span>
            <span class="grow truncate font-sans min-inline-0"
              >{options[entry.option]?.name}</span
            >
            <span
              >{focus.total > 0
                ? percent(entry.votes / focus.total)
                : "–"}</span
            >
          </span>
        {/each}
        <span class="text-xs text-muted-foreground">
          {focus.total > 0
            ? m.live_votes_counted({ count: count(focus.total) })
            : m.live_waiting()}
        </span>
      </div>
    {/if}
  </div>
  <figcaption
    class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.8125rem] text-muted-foreground"
  >
    <span class="font-semibold text-foreground"
      >{level === "us.nyc_ed"
        ? m.live_map_ed()
        : level === "us.nyc_ad"
          ? m.live_map_ad()
          : m.live_map_county()}</span
    >
    {#each leaders as leader (leader)}
      <span class="flex items-center gap-1.5">
        <span
          class="flex overflow-clip rounded-xs block-2.5 inline-6"
          aria-hidden="true"
        >
          <span
            class="grow"
            style:background-color="color-mix(in oklab, {tones[leader]} 30%, var(--card))"
          ></span>
          <span class="grow" style:background-color={tones[leader]}></span>
        </span>
        {options[leader]?.name}
      </span>
    {/each}
    <span>{m.live_map_legend()}</span>
  </figcaption>
</figure>
