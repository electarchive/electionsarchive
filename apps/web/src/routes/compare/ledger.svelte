<script
  lang="ts"
  generics="Cell extends { key: string; row: number; year: number; href: string | null; halves: [{ color: string; strength: number } | null, { color: string; strength: number } | null] }"
>
  import type { Snippet } from "svelte";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { marginShade, safety } from "#lib/shade.js";
  import { cn } from "#lib/utils.js";

  const {
    rows,
    cells,
    label,
    readout,
  }: {
    rows: Array<string>;
    cells: Array<Cell>;
    label: string;
    readout: Snippet<[Cell]>;
  } = $props();

  const AXIS = 18;

  let width = $state(0);
  let picked = $state<string | null>(null);

  const years = $derived(
    [...new Set(cells.map((cell) => cell.year))].toSorted((a, b) => a - b),
  );
  const columns = $derived(new Map(years.map((year, index) => [year, index])));
  const size = $derived(
    Math.max(12, Math.min(28, Math.floor(width / Math.max(1, years.length)))),
  );
  const grid = $derived(
    new Map(
      cells.map((cell) => [`${cell.row}:${columns.get(cell.year) ?? 0}`, cell]),
    ),
  );
  const ordered = $derived(
    cells.toSorted((a, b) => a.year - b.year || a.row - b.row),
  );
  const active = $derived(
    cells.find((cell) => cell.key === picked) ?? ordered.at(-1) ?? null,
  );
  const ticks = $derived.by(() => {
    const shown: Array<{ year: number; x: number }> = [];
    for (const [index, year] of years.entries()) {
      const x = index * size;
      const last = shown.at(-1);
      const decade = Math.floor(year / 10);
      if (
        last == null ||
        (decade !== Math.floor(last.year / 10) && x - last.x >= 40)
      ) {
        shown.push({ year, x });
      }
    }
    return shown;
  });

  const polygon = (cell: Cell, side: number) => {
    const x = (columns.get(cell.year) ?? 0) * size + 0.5;
    const y = AXIS + cell.row * size + 0.5;
    const s = size - 2;
    return side === 0
      ? `${x},${y} ${x + s},${y} ${x},${y + s}`
      : `${x + s},${y} ${x + s},${y + s} ${x},${y + s}`;
  };

  function locate(event: MouseEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const column = Math.floor((event.clientX - box.left) / size);
    const row = Math.floor((event.clientY - box.top - AXIS) / size);
    return grid.get(`${row}:${column}`) ?? null;
  }

  function open(cell: Cell | null) {
    if (cell?.href != null) {
      void goto(resolve("/[...path]", { path: cell.href }));
    }
  }

  function step(event: KeyboardEvent) {
    if (active == null) {
      return;
    }
    if (event.key === "Enter") {
      open(active);
      return;
    }
    const index = ordered.indexOf(active);
    const moves: Record<string, () => Cell | undefined> = {
      ArrowRight: () =>
        ordered.slice(index + 1).find((cell) => cell.row === active.row),
      ArrowLeft: () =>
        ordered.slice(0, index).findLast((cell) => cell.row === active.row),
      ArrowDown: () =>
        cells.find(
          (cell) => cell.year === active.year && cell.row === active.row + 1,
        ),
      ArrowUp: () =>
        cells.find(
          (cell) => cell.year === active.year && cell.row === active.row - 1,
        ),
    };
    const next = moves[event.key]?.();
    if (moves[event.key] != null) {
      event.preventDefault();
    }
    if (next != null) {
      picked = next.key;
    }
  }
</script>

<div class="grid gap-2">
  <div class="grid grid-cols-[minmax(5.5rem,9rem)_minmax(0,1fr)] gap-x-2">
    <ul
      class="grid content-start text-xs font-semibold"
      style:padding-block-start="{AXIS}px"
    >
      {#each rows as row, index (index)}
        <li
          class={cn("flex items-center justify-end truncate text-end", {
            "text-muted-foreground": active?.row !== index,
          })}
          style:block-size="{size}px"
        >
          <span class="truncate">{row}</span>
        </li>
      {/each}
    </ul>
    <div
      class="overflow-x-auto overscroll-x-contain min-inline-0"
      bind:clientWidth={width}
    >
      {#if width > 0}
        <svg
          width={years.length * size}
          height={AXIS + rows.length * size}
          class="block cursor-pointer touch-manipulation outline-none focus-visible:ring-2 focus-visible:ring-ring"
          role="grid"
          aria-label={label}
          tabindex="0"
          onpointermove={(event) => {
            const cell = locate(event);
            if (cell != null && event.pointerType === "mouse") {
              picked = cell.key;
            }
          }}
          onclick={(event) => {
            const cell = locate(event);
            if (cell == null) {
              return;
            }
            if (cell.key === active?.key) {
              open(cell);
            } else {
              picked = cell.key;
            }
          }}
          onkeydown={step}
        >
          {#each ticks as tick (tick.year)}
            <text
              x={tick.x}
              y={AXIS - 6}
              class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
              >{tick.year}</text
            >
            <line
              x1={tick.x + 0.5}
              x2={tick.x + 0.5}
              y1={AXIS - 4}
              y2={AXIS + rows.length * size}
              class="stroke-border"
            />
          {/each}
          {#if active != null}
            <rect
              x={(columns.get(active.year) ?? 0) * size - 1}
              y={AXIS - 2}
              width={size}
              height={rows.length * size + 2}
              class="fill-accent"
            />
          {/if}
          {#each cells as cell (cell.key)}
            {#each cell.halves as half, side (side)}
              {#if half == null}
                <polygon
                  points={polygon(cell, side)}
                  class="fill-none stroke-border"
                />
              {:else}
                <polygon
                  points={polygon(cell, side)}
                  fill={marginShade(
                    half.color,
                    safety(0.16 + 0.84 * half.strength),
                  )}
                />
              {/if}
            {/each}
          {/each}
          {#if active != null}
            <rect
              x={(columns.get(active.year) ?? 0) * size - 0.5}
              y={AXIS + active.row * size - 0.5}
              width={size}
              height={size}
              class="fill-none stroke-foreground stroke-[1.5]"
            />
          {/if}
        </svg>
      {/if}
    </div>
  </div>
  {#if active != null}
    <div
      class="grid border-bs border-border pbs-2 min-block-12"
      aria-live="polite"
    >
      {@render readout(active)}
    </div>
  {/if}
</div>
