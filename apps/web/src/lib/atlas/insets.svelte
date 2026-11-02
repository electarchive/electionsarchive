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
  import { fade } from "svelte/transition";
  import type { Lens } from "#lib/atlas/color.js";
  import type { Hit } from "#lib/atlas/stage.svelte.js";
  import { tone } from "#lib/atlas/color.js";

  const {
    groups,
    lens,
    picked,
    hovered,
    room,
    isWide,
    isCalm,
    onhover,
    onpick,
  }: {
    groups: Array<{ tier: number; tiles: string; hits: Array<Hit> }>;
    lens: Lens;
    picked: Array<number>;
    hovered: Hit | null;
    room: { width: number; height: number };
    isWide: boolean;
    isCalm: boolean;
    onhover: (hit: Hit | null, point: { x: number; y: number } | null) => void;
    onpick: (hit: Hit, isMany: boolean) => void;
  } = $props();

  const tiers = $derived.by(() => {
    const found = new Map<
      number,
      {
        sets: Array<{ tiles: string; ks: Array<number> }>;
        hits: Map<number, Hit>;
      }
    >();
    for (const group of groups) {
      const held = found.get(group.tier) ?? {
        sets: new Array<{ tiles: string; ks: Array<number> }>(),
        hits: new Map<number, Hit>(),
      };
      found.set(group.tier, held);
      const ks: Array<number> = [];
      for (const hit of group.hits) {
        const k = hit.set.places.k[hit.i];
        if (k != null) {
          ks.push(k);
          held.hits.set(k, hit);
        }
      }
      if (held.sets.length < 4) {
        held.sets.push({ tiles: group.tiles, ks: ks.slice(0, 2500) });
      }
    }
    return found;
  });
  const wanted = $derived(
    JSON.stringify(
      [...tiers].map(([tier, held]) => [
        tier,
        held.sets.map((set) => [set.tiles, set.ks.length]),
      ]),
    ),
  );
  let found = $state.raw<Array<{ tier: number; boxes: Boxes }>>([]);

  $effect(() => {
    void wanted;
    let isCurrent = true;
    const asks = [...tiers].map(([tier, held]) => ({ tier, sets: held.sets }));
    void Promise.all(
      asks.map(async (ask) => ({
        tier: ask.tier,
        boxes: await draw(ask.sets),
      })),
    ).then(
      (next) => {
        if (isCurrent) {
          found = next;
        }
      },
      () => {},
    );
    return () => {
      isCurrent = false;
    };
  });

  const stature = $derived(
    Math.min(isWide ? 92 : 64, Math.max(40, room.height * 0.13)),
  );
  const panes = $derived.by(() => {
    const order: Array<string> = [];
    const chosen = new Map<
      string,
      { tier: number; box: Boxes[number]; hits: Map<number, Hit> }
    >();
    for (const { tier, boxes } of found.toSorted((a, b) => a.tier - b.tier)) {
      const hits = tiers.get(tier)?.hits ?? new Map<number, Hit>();
      for (const box of boxes) {
        if (!chosen.has(box.name)) {
          order.push(box.name);
        }
        chosen.set(box.name, { tier, box, hits });
      }
    }
    const shaped = order.flatMap((name) => {
      const held = chosen.get(name);
      if (held == null) {
        return [];
      }
      const { box, hits } = held;
      const pad = Math.max(box.width, box.height) * 0.05;
      const width = box.width + pad * 2;
      const height = box.height + pad * 2;
      return [
        {
          name,
          units: box.units.map((unit) => ({ ...unit, hit: hits.get(unit.k) })),
          view: `${-pad} ${-pad} ${width} ${height}`,
          ratio: width / height,
        },
      ];
    });
    let total = 0;
    for (const box of shaped) {
      total += stature * box.ratio + 6;
    }
    const space = isWide ? room.width * 0.5 - 72 : room.width - 84;
    const tallest = stature * Math.min(1, space / Math.max(total, 1));
    return shaped.map((box) => ({
      ...box,
      inline: tallest * box.ratio,
      block: tallest,
    }));
  });
  const tour = $derived(
    panes.flatMap((box) =>
      box.units.flatMap((unit) => (unit.hit == null ? [] : [unit.k])),
    ),
  );
  const chosen = $derived(new Set(picked));
  const lit = $derived(
    hovered == null ? null : (hovered.set.places.k[hovered.i] ?? null),
  );
  let focused = $state<number | null>(null);
  const stop = $derived(
    focused != null && tour.includes(focused) ? focused : (tour[0] ?? null),
  );

  function spot(target: Element, event?: PointerEvent) {
    const bounds = target
      .closest("[data-insets]")
      ?.parentElement?.getBoundingClientRect();
    if (bounds == null) {
      return null;
    }
    if (event != null) {
      return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    }
    const rect = target.getBoundingClientRect();
    return {
      x: rect.left - bounds.left + rect.width / 2,
      y: rect.top - bounds.top + rect.height / 2,
    };
  }

  function step(target: Element, k: number, delta: number) {
    const at = tour.indexOf(k);
    const next = tour[(at + delta + tour.length) % tour.length];
    if (next == null) {
      return;
    }
    focused = next;
    target
      .closest("[data-insets]")
      ?.querySelector<SVGPathElement>(`[data-k="${next}"]`)
      ?.focus();
  }
</script>

{#if panes.length > 0}
  <div
    class="pointer-events-none flex items-end gap-1.5"
    data-insets
    transition:fade={{ duration: isCalm ? 0 : 150 }}
  >
    {#each panes as box (box.name)}
      <div
        class="pointer-events-auto grid overflow-hidden rounded-sm bg-card shadow-menu ring-1 ring-border"
        role="group"
        aria-label={box.name}
        onpointerleave={() => onhover(null, null)}
      >
        <span
          class="truncate px-1.5 pbs-1 text-[0.625rem] leading-none font-semibold text-muted-foreground max-inline-(--inline)"
          style:--inline="{box.inline}px">{box.name}</span
        >
        <svg
          class="block"
          viewBox={box.view}
          style:inline-size="{box.inline}px"
          style:block-size="{box.block}px"
        >
          {#each box.units as unit (unit.k)}
            {const hit = $derived(unit.hit)}
            {#if hit == null}
              <path
                d={unit.d}
                fill="color-mix(in oklab, var(--muted-foreground) 14%, var(--card))"
                stroke="color-mix(in oklab, var(--muted-foreground) 34%, var(--card))"
                stroke-width="0.75"
                stroke-linejoin="round"
                vector-effect="non-scaling-stroke"
              />
            {:else}
              {const fill = $derived(tone(hit.set, hit.i, lens))}
              <path
                class="cursor-pointer outline-none"
                d={unit.d}
                {fill}
                stroke="var(--card)"
                stroke-width="0.6"
                stroke-linejoin="round"
                vector-effect="non-scaling-stroke"
                role="button"
                tabindex={unit.k === stop ? 0 : -1}
                aria-label={hit.set.places.name[hit.i] ?? box.name}
                aria-pressed={chosen.has(unit.k)}
                data-k={unit.k}
                onpointermove={(event) => {
                  if (event.pointerType === "mouse") {
                    onhover(hit, spot(event.currentTarget, event));
                  }
                }}
                onfocus={(event) => {
                  focused = unit.k;
                  onhover(hit, spot(event.currentTarget));
                }}
                onblur={() => onhover(null, null)}
                onclick={(event) =>
                  onpick(hit, event.shiftKey || event.metaKey || event.ctrlKey)}
                onkeydown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onpick(hit, event.shiftKey);
                  } else if (
                    event.key === "ArrowRight" ||
                    event.key === "ArrowDown"
                  ) {
                    event.preventDefault();
                    step(event.currentTarget, unit.k, 1);
                  } else if (
                    event.key === "ArrowLeft" ||
                    event.key === "ArrowUp"
                  ) {
                    event.preventDefault();
                    step(event.currentTarget, unit.k, -1);
                  }
                }}
              />
            {/if}
          {/each}
          {#each box.units as unit (unit.k)}
            {#if unit.hit != null && (chosen.has(unit.k) || unit.k === lit)}
              <path
                class="pointer-events-none"
                d={unit.d}
                fill="none"
                stroke="var(--foreground)"
                stroke-width={chosen.has(unit.k) ? 2 : 1.25}
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
