<script lang="ts">
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import ArrowUpDownIcon from "@lucide/svelte/icons/arrow-up-down";
  import CopyCheckIcon from "@lucide/svelte/icons/copy-check";
  import ImageDownIcon from "@lucide/svelte/icons/image-down";
  import LinkIcon from "@lucide/svelte/icons/link";
  import XIcon from "@lucide/svelte/icons/x";
  import Download from "#lib/components/download.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Row = {
    k: number;
    name: string;
    where: string;
    valid: number;
    first: number | null;
    second: number | null;
    margin: number | null;
    before: number | null;
    swing: number | null;
    tone: string;
  };
  type Column = "name" | "valid" | "first" | "second" | "margin" | "swing";

  const {
    rows,
    sides,
    title,
    year,
    now,
    picked,
    columns,
    table,
    resolve,
    onpick,
    onhover,
    onimage,
    onclose,
  }: {
    rows: Array<Row>;
    sides: [string, string];
    title: string;
    year: string | null;
    now: string | null;
    picked: Array<number>;
    columns: Array<string>;
    table: () => Array<Array<string | number | null>>;
    resolve: (css: string) => string;
    onpick: (ks: Array<number>) => void;
    onhover: (k: number | null) => void;
    onimage: () => void;
    onclose: () => void;
  } = $props();

  const LIMIT = 250;
  const PAD = { top: 10, right: 12, bottom: 30, left: 44 };

  let tab = $state<"table" | "scatter">("table");
  let column = $state<Column>("valid");
  let isDescending = $state(true);
  let isCopied = $state(false);
  let width = $state(0);
  let height = $state(0);
  let near = $state<Row | null>(null);
  let brush = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(
    null,
  );

  const chosen = $derived(new Set(picked));
  const isScatter = $derived(tab === "scatter" && year != null);
  const sorted = $derived.by(() => {
    const sign = isDescending ? -1 : 1;
    return rows.toSorted((a, b) => {
      if (column === "name") {
        return sign * a.name.localeCompare(b.name);
      }
      const x = a[column];
      const y = b[column];
      if (x == null || y == null) {
        return x == null ? (y == null ? 0 : 1) : -1;
      }
      return sign * (x - y);
    });
  });
  const shown = $derived(sorted.slice(0, LIMIT));
  const dots = $derived(
    rows.filter((row) => row.margin != null && row.before != null),
  );
  const reach = $derived.by(() => {
    let most = 0;
    for (const row of dots) {
      most = Math.max(
        most,
        Math.abs(row.margin ?? 0),
        Math.abs(row.before ?? 0),
      );
    }
    return Math.min(1, Math.max(0.1, Math.ceil(most * 10) / 10));
  });
  const heaviest = $derived.by(() => {
    let most = 0;
    for (const row of dots) {
      most = Math.max(most, row.valid);
    }
    return most;
  });
  const plot = $derived({
    w: Math.max(0, width - PAD.left - PAD.right),
    h: Math.max(0, height - PAD.top - PAD.bottom),
  });
  const ticks = $derived([-reach, -reach / 2, 0, reach / 2, reach]);
  const heads = $derived<Array<{ key: Column; label: string }>>([
    { key: "name", label: m.atlas_column_place() },
    { key: "valid", label: m.atlas_column_votes() },
    { key: "first", label: sides[0] },
    { key: "second", label: sides[1] },
    { key: "margin", label: m.atlas_column_margin() },
    ...(year == null
      ? []
      : [{ key: "swing" as const, label: m.atlas_column_swing() }]),
  ]);

  function sx(value: number) {
    return PAD.left + ((value + reach) / (2 * reach)) * plot.w;
  }

  function sy(value: number) {
    return PAD.top + ((reach - value) / (2 * reach)) * plot.h;
  }

  function size(valid: number) {
    return heaviest === 0
      ? 2
      : Math.max(1.5, Math.sqrt(valid / heaviest) * Math.min(12, plot.h / 14));
  }

  function sort(next: Column) {
    if (column === next) {
      isDescending = !isDescending;
      return;
    }
    column = next;
    isDescending = next !== "name";
  }

  async function copy() {
    await navigator.clipboard.writeText(location.href);
    isCopied = true;
    setTimeout(() => {
      isCopied = false;
    }, 1600);
  }

  function locate(event: PointerEvent) {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function closest(x: number, y: number) {
    let best: Row | null = null;
    let gap = 12 ** 2;
    for (const row of dots) {
      const dx = sx(row.before ?? 0) - x;
      const dy = sy(row.margin ?? 0) - y;
      const d = dx * dx + dy * dy;
      if (d < gap) {
        gap = d;
        best = row;
      }
    }
    return best;
  }

  function press(event: PointerEvent) {
    if (event.button !== 0) {
      return;
    }
    const { x, y } = locate(event);
    (event.currentTarget as SVGSVGElement).setPointerCapture(event.pointerId);
    brush = { x0: x, y0: y, x1: x, y1: y };
  }

  function move(event: PointerEvent) {
    const { x, y } = locate(event);
    if (brush != null) {
      brush = { ...brush, x1: x, y1: y };
      return;
    }
    const found = closest(x, y);
    if (found !== near) {
      near = found;
      onhover(found?.k ?? null);
    }
  }

  function release() {
    const held = brush;
    brush = null;
    if (held == null) {
      return;
    }
    const left = Math.min(held.x0, held.x1);
    const right = Math.max(held.x0, held.x1);
    const top = Math.min(held.y0, held.y1);
    const bottom = Math.max(held.y0, held.y1);
    if (right - left < 4 && bottom - top < 4) {
      const found = closest(held.x0, held.y0);
      onpick(
        found == null || (picked.length === 1 && picked[0] === found.k)
          ? []
          : [found.k],
      );
      return;
    }
    const inside: Array<number> = [];
    for (const row of dots) {
      const x = sx(row.before ?? 0);
      const y = sy(row.margin ?? 0);
      if (x >= left && x <= right && y >= top && y <= bottom) {
        inside.push(row.k);
      }
    }
    onpick(inside.slice(0, 5000));
  }

  function paint(canvas: HTMLCanvasElement) {
    const pen = canvas.getContext("2d");
    if (pen == null) {
      return;
    }
    const ratio = devicePixelRatio;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    pen.setTransform(ratio, 0, 0, ratio, 0, 0);
    pen.clearRect(0, 0, width, height);
    const isPicking = chosen.size > 0;
    const halo = resolve("var(--card)");
    const order = dots.toSorted(
      (a, b) =>
        Number(chosen.has(a.k)) - Number(chosen.has(b.k)) || b.valid - a.valid,
    );
    for (const row of order) {
      const isOn = !isPicking || chosen.has(row.k);
      pen.globalAlpha = isOn ? 0.78 : 0.14;
      pen.beginPath();
      pen.arc(
        sx(row.before ?? 0),
        sy(row.margin ?? 0),
        size(row.valid),
        0,
        2 * Math.PI,
      );
      pen.fillStyle = resolve(row.tone);
      pen.fill();
      pen.lineWidth = 0.75;
      pen.strokeStyle = halo;
      pen.stroke();
    }
    pen.globalAlpha = 1;
  }
</script>

<section
  class="@container grid grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] overflow-clip block-full"
  aria-label={m.atlas_analysis()}
>
  <header
    class="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-be px-3 py-2"
  >
    {#if year != null}
      <div class="order-last basis-full @2xl:order-none @2xl:basis-auto">
        <Segmented
          label={m.atlas_analysis()}
          size="sm"
          options={[
            { value: "table", label: m.atlas_table() },
            { value: "scatter", label: m.atlas_scatter() },
          ]}
          value={tab}
          onValueChange={(value) => {
            tab = value;
            near = null;
            onhover(null);
          }}
        />
      </div>
    {/if}
    <h2 class="flex-1 truncate text-[0.8125rem] font-semibold min-inline-0">
      {title}
    </h2>
    <Download name="places" {columns} rows={table} />
    <button
      type="button"
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground pointer-coarse:block-10 pointer-coarse:inline-10"
      aria-label={m.atlas_image()}
      title={m.atlas_image()}
      onclick={onimage}
    >
      <ImageDownIcon class="block-4 inline-4" />
    </button>
    <button
      type="button"
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground pointer-coarse:block-10 pointer-coarse:inline-10"
      aria-label={isCopied ? m.atlas_copied() : m.atlas_link()}
      title={isCopied ? m.atlas_copied() : m.atlas_link()}
      onclick={copy}
    >
      {#if isCopied}
        <CopyCheckIcon class="text-foreground block-4 inline-4" />
      {:else}
        <LinkIcon class="block-4 inline-4" />
      {/if}
    </button>
    <button
      type="button"
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground pointer-coarse:block-10 pointer-coarse:inline-10"
      aria-label={m.atlas_analysis_close()}
      title={m.atlas_analysis_close()}
      onclick={onclose}
    >
      <XIcon class="block-4 inline-4" />
    </button>
  </header>
  {#if rows.length === 0}
    <p class="p-3 text-[0.8125rem] text-muted-foreground">
      {m.atlas_none_match()}
    </p>
  {:else if isScatter}
    <div
      class="grid grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)_auto] gap-1 p-2"
    >
      <div
        class="relative min-block-40"
        bind:clientWidth={width}
        bind:clientHeight={height}
      >
        <svg
          class="absolute inset-0 overflow-visible text-muted-foreground block-full inline-full"
          aria-hidden="true"
        >
          {#each ticks as tick (tick)}
            <line
              x1={sx(tick)}
              x2={sx(tick)}
              y1={PAD.top}
              y2={PAD.top + plot.h}
              stroke="currentColor"
              stroke-opacity={tick === 0 ? 0.45 : 0.12}
            />
            <line
              x1={PAD.left}
              x2={PAD.left + plot.w}
              y1={sy(tick)}
              y2={sy(tick)}
              stroke="currentColor"
              stroke-opacity={tick === 0 ? 0.45 : 0.12}
            />
            <text
              x={sx(tick)}
              y={PAD.top + plot.h + 14}
              text-anchor="middle"
              class="fill-current font-figures text-[0.6875rem] tabular-nums"
              >{points(tick, 0)}</text
            >
            <text
              x={PAD.left - 6}
              y={sy(tick) + 4}
              text-anchor="end"
              class="fill-current font-figures text-[0.6875rem] tabular-nums"
              >{points(tick, 0)}</text
            >
          {/each}
          <line
            x1={sx(-reach)}
            y1={sy(-reach)}
            x2={sx(reach)}
            y2={sy(reach)}
            stroke="currentColor"
            stroke-opacity="0.55"
            stroke-dasharray="3 3"
          />
          <text
            x={PAD.left + plot.w}
            y={PAD.top + plot.h + 27}
            text-anchor="end"
            class="fill-current text-[0.6875rem]"
            >{m.atlas_margin_in({ year: year ?? "" })}</text
          >
          <text
            x={PAD.left + 4}
            y={PAD.top + 11}
            class="fill-current text-[0.6875rem]"
            >{m.atlas_margin_in({ year: now ?? "" })}</text
          >
        </svg>
        <canvas
          {@attach paint}
          class="pointer-events-none absolute inset-0 block-full inline-full"
        ></canvas>
        <svg
          role="presentation"
          class="absolute inset-0 cursor-crosshair touch-none block-full inline-full"
          onpointerdown={press}
          onpointermove={move}
          onpointerup={release}
          onpointercancel={() => {
            brush = null;
          }}
          onpointerleave={() => {
            if (brush == null && near != null) {
              near = null;
              onhover(null);
            }
          }}
        >
          {#if near != null && brush == null}
            {const cx = $derived(sx(near.before ?? 0))}
            {const cy = $derived(sy(near.margin ?? 0))}
            <circle
              {cx}
              {cy}
              r={size(near.valid) + 2.5}
              fill="none"
              class="stroke-foreground"
              stroke-width="1.5"
            />
            <foreignObject
              x={cx > PAD.left + plot.w / 2 ? cx - 196 : cx + 12}
              y={Math.max(0, Math.min(height - 52, cy - 26))}
              width="184"
              height="52"
            >
              <div
                class="grid rounded-sm border bg-popover px-2 py-1 text-[0.6875rem] text-popover-foreground shadow-sm"
              >
                <span class="truncate font-semibold">{near.name}</span>
                <span class="flex gap-3 font-figures tabular-nums">
                  <span>{year} {points(near.before ?? 0)}</span>
                  <span>{now} {points(near.margin ?? 0)}</span>
                </span>
              </div>
            </foreignObject>
          {/if}
          {#if brush != null}
            <rect
              x={Math.min(brush.x0, brush.x1)}
              y={Math.min(brush.y0, brush.y1)}
              width={Math.abs(brush.x1 - brush.x0)}
              height={Math.abs(brush.y1 - brush.y0)}
              class="fill-foreground/5 stroke-foreground"
              stroke-dasharray="3 2"
            />
          {/if}
        </svg>
      </div>
      <p class="px-1 text-xs text-muted-foreground">
        {m.atlas_scatter_hint({ name: sides[0] })}
      </p>
    </div>
  {:else}
    <div class="overflow-auto overscroll-contain">
      <table class="border-collapse text-[0.8125rem] inline-full">
        <thead
          class="sticky inset-bs-0 z-1 bg-card text-xs text-muted-foreground"
        >
          <tr class="border-be">
            {#each heads as head (head.key)}
              <th
                scope="col"
                class={cn("p-0 font-medium", {
                  "sticky inset-s-0 z-2 bg-card text-start":
                    head.key === "name",
                  "text-end whitespace-nowrap": head.key !== "name",
                })}
                aria-sort={column === head.key
                  ? isDescending
                    ? "descending"
                    : "ascending"
                  : "none"}
              >
                <button
                  type="button"
                  class={cn(
                    "inline-flex items-center gap-1 px-2 py-1.5 max-inline-40 hover:text-foreground",
                    { "flex-row-reverse": head.key !== "name" },
                    { "text-foreground": column === head.key },
                  )}
                  onclick={() => sort(head.key)}
                >
                  <span class="truncate">{head.label}</span>
                  {#if column !== head.key}
                    <ArrowUpDownIcon
                      class="shrink-0 opacity-40 block-3 inline-3"
                    />
                  {:else if isDescending}
                    <ArrowDownIcon class="shrink-0 block-3 inline-3" />
                  {:else}
                    <ArrowUpIcon class="shrink-0 block-3 inline-3" />
                  {/if}
                </button>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody class="font-figures tabular-nums">
          {#each shown as row (row.k)}
            <tr
              class={cn("border-be bg-card hover:bg-secondary", {
                "bg-secondary": chosen.has(row.k),
              })}
              onpointerenter={() => onhover(row.k)}
              onpointerleave={() => onhover(null)}
            >
              <th
                scope="row"
                class="sticky inset-s-0 z-1 bg-inherit p-0 text-start font-normal"
              >
                <button
                  type="button"
                  class="grid px-2 py-1 text-start max-inline-64 min-inline-32"
                  onclick={(event) =>
                    onpick(
                      event.shiftKey || event.metaKey
                        ? chosen.has(row.k)
                          ? picked.filter((k) => k !== row.k)
                          : [...picked, row.k]
                        : [row.k],
                    )}
                  onfocus={() => onhover(row.k)}
                  onblur={() => onhover(null)}
                >
                  <span class="truncate font-sans font-semibold"
                    >{row.name}</span
                  >
                  <span class="truncate font-sans text-xs text-muted-foreground"
                    >{row.where}</span
                  >
                </button>
              </th>
              <td class="px-2 py-1 text-end">{count(row.valid)}</td>
              <td class="px-2 py-1 text-end"
                >{row.first == null ? "–" : percent(row.first)}</td
              >
              <td class="px-2 py-1 text-end"
                >{row.second == null ? "–" : percent(row.second)}</td
              >
              <td class="px-2 py-1 text-end">
                {#if row.margin != null}
                  <span class="inline-flex items-center gap-1.5">
                    <span
                      class="shrink-0 rounded-xs block-2 inline-2"
                      style:background={row.tone}
                    ></span>
                    {points(row.margin)}
                  </span>
                {:else}
                  –
                {/if}
              </td>
              {#if year != null}
                <td class="px-2 py-1 text-end"
                  >{row.swing == null ? "–" : points(row.swing)}</td
                >
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
      {#if sorted.length > LIMIT}
        <p class="px-3 py-2 text-xs text-muted-foreground">
          {m.atlas_showing({
            shown: count(LIMIT),
            total: count(sorted.length),
          })}
        </p>
      {/if}
    </div>
  {/if}
</section>
