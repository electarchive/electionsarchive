<script lang="ts" module>
  import { orpc } from "#lib/rpc.js";

  type Boxes = Awaited<ReturnType<typeof orpc.maps.apart.call>>;

  const drawn = new Map<string, Promise<Boxes>>();

  async function draw(sets: Array<{ tiles: string; ks: Array<number> }>) {
    const key = JSON.stringify(sets);
    let found = drawn.get(key);
    if (found == null) {
      found = orpc.maps.apart.call({ sets });
      drawn.set(key, found);
      found.catch(() => {
        if (drawn.get(key) === found) {
          drawn.delete(key);
        }
      });
    }
    return found;
  }
</script>

<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Fill } from "./model.js";
  import { MUTED } from "./model.js";

  let {
    sets,
    fills,
    selected,
    hovered = $bindable(null),
    stature,
    nameOf,
    labelOf,
    onPick,
    tip,
  }: {
    sets: Array<{
      tiles: string;
      places: Array<{ areaId: string; k: number }>;
    }>;
    fills: Map<string, Fill>;
    selected: string | null;
    hovered?: string | null;
    stature: number;
    nameOf: (name: string) => string;
    labelOf: (areaId: string) => string;
    onPick: (areaId: string) => void;
    tip: Snippet<[string]>;
  } = $props();

  const areaOf = $derived(
    new Map(
      sets.flatMap((set) =>
        set.places.map((place) => [place.k, place.areaId] as const),
      ),
    ),
  );
  const asked = $derived(
    sets.flatMap((set) =>
      set.places.length === 0
        ? []
        : [
            {
              tiles: set.tiles,
              ks: set.places.map((place) => place.k).toSorted((a, b) => a - b),
            },
          ],
    ),
  );
  const wanted = $derived(JSON.stringify(asked));

  let boxes = $state.raw<{ key: string; value: Boxes } | null>(null);
  let lit = $state<string | null>(null);

  $effect(() => {
    const key = wanted;
    if (asked.length === 0) {
      return;
    }
    let isCurrent = true;
    void draw(asked).then(
      (value) => {
        if (isCurrent) {
          boxes = { key, value };
        }
      },
      () => {},
    );
    return () => {
      isCurrent = false;
    };
  });

  const panes = $derived(
    (boxes?.key === wanted ? boxes.value : []).flatMap((box) => {
      const pad = Math.max(box.width, box.height) * 0.06;
      const width = box.width + pad * 2;
      const height = box.height + pad * 2;
      const units = box.units.flatMap((unit) => {
        const areaId = areaOf.get(unit.k);
        return areaId == null ? [] : [{ ...unit, areaId }];
      });
      return units.length === 0
        ? []
        : [
            {
              name: nameOf(box.name),
              units,
              view: `${-pad} ${-pad} ${width} ${height}`,
              inline: Math.min(stature * 2.2, (stature * width) / height),
            },
          ];
    }),
  );
  const tour = $derived(
    panes.flatMap((box) => box.units.map((unit) => unit.areaId)),
  );

  function step(target: Element, areaId: string, delta: number) {
    const at = tour.indexOf(areaId);
    const next = tour[(at + delta + tour.length) % tour.length];
    if (next == null) {
      return;
    }
    target
      .closest("[data-insets]")
      ?.querySelector<SVGPathElement>(`[data-area="${next}"]`)
      ?.focus();
  }

  function enter(areaId: string | null) {
    lit = areaId;
    hovered = areaId;
  }
</script>

{#if panes.length > 0}
  <div class="pointer-events-none relative flex items-end gap-1.5" data-insets>
    {#if lit != null}
      <div
        class="absolute inset-s-0 inset-be-[calc(100%+0.5rem)] z-30 grid gap-1.5 rounded-sm bg-popover/95 px-3 py-2.5 text-[0.8125rem] shadow-menu ring-1 ring-border backdrop-blur-sm inline-60"
      >
        {@render tip(lit)}
      </div>
    {/if}
    {#each panes as box, index (box.name)}
      <div
        class="pointer-events-auto grid overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border backdrop-blur-sm"
        role="group"
        aria-label={box.name}
        onpointerleave={() => enter(null)}
      >
        <span
          class="truncate px-1.5 pbs-1 text-[0.625rem]/none font-semibold text-muted-foreground max-inline-(--inline)"
          style:--inline="{box.inline}px">{box.name}</span
        >
        <svg
          class="block"
          viewBox={box.view}
          style:inline-size="{box.inline}px"
          style:block-size="{stature}px"
        >
          {#each box.units as unit, position (unit.k)}
            <path
              class="cursor-pointer outline-none"
              d={unit.d}
              fill={fills.get(unit.areaId)?.color ?? MUTED}
              stroke="var(--map-seam)"
              stroke-width="0.6"
              stroke-linejoin="round"
              vector-effect="non-scaling-stroke"
              role="button"
              tabindex={index === 0 && position === 0 ? 0 : -1}
              aria-label={labelOf(unit.areaId)}
              aria-pressed={selected === unit.areaId}
              data-area={unit.areaId}
              onpointermove={(event) => {
                if (event.pointerType === "mouse" && lit !== unit.areaId) {
                  enter(unit.areaId);
                }
              }}
              onfocus={() => enter(unit.areaId)}
              onblur={() => enter(null)}
              onclick={() => onPick(unit.areaId)}
              onkeydown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onPick(unit.areaId);
                } else if (
                  event.key === "ArrowRight" ||
                  event.key === "ArrowDown"
                ) {
                  event.preventDefault();
                  step(event.currentTarget, unit.areaId, 1);
                } else if (
                  event.key === "ArrowLeft" ||
                  event.key === "ArrowUp"
                ) {
                  event.preventDefault();
                  step(event.currentTarget, unit.areaId, -1);
                }
              }}
            />
          {/each}
          {#each box.units as unit (unit.k)}
            {#if selected === unit.areaId || hovered === unit.areaId}
              <path
                class="pointer-events-none"
                d={unit.d}
                fill="none"
                stroke="var(--foreground)"
                stroke-width={selected === unit.areaId ? 2 : 1.25}
                stroke-linejoin="round"
                vector-effect="non-scaling-stroke"
              />
            {/if}
          {/each}
        </svg>
      </div>
    {/each}
  </div>
{/if}
