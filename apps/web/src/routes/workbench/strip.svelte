<script lang="ts">
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { Contest, Desk } from "./desk.svelte.js";

  const { contest, desk }: { contest: Contest; desk: Desk } = $props();

  const PAD = 10;
  const HEAD = 16;
  const TICKS = [0, 0.25, 0.5, 0.75, 1];

  let width = $state(0);
  let height = $state(0);

  const legend = $derived(contest.places?.legend ?? []);
  const places = $derived(contest.places?.rows ?? []);
  const overall = $derived(
    new Map(contest.rows.map((row) => [row.key, row.current?.share ?? 0])),
  );
  const lanes = $derived(
    legend
      .map((entry, index) => ({
        ...entry,
        index,
        share: overall.get(entry.key) ?? 0,
      }))
      .toSorted((a, b) => b.share - a.share)
      .slice(0, Math.max(1, Math.min(4, Math.floor((height - 14) / 64)))),
  );
  const band = $derived(lanes.length === 0 ? 0 : (height - 14) / lanes.length);
  const most = $derived.by(() => {
    let found = 1;
    for (const row of places) {
      found = Math.max(found, row.valid);
    }
    return found;
  });
  const scale = $derived(Math.min(1, Math.max(0.5, width / 640)));
  const x = (share: number) => PAD + share * (width - PAD * 2);

  function dodge(
    items: Array<{ name: string; x: number; r: number }>,
    room: number,
  ) {
    const placed: Array<{ name: string; x: number; y: number; r: number }> = [];
    const bins = new Map<number, Array<number>>();
    const size = 12 * scale;
    for (const item of items.toSorted((a, b) => b.r - a.r)) {
      const bin = Math.floor(item.x / size);
      const near: Array<{ x: number; y: number; r: number }> = [];
      for (let step = bin - 1; step <= bin + 1; step += 1) {
        for (const at of bins.get(step) ?? []) {
          const other = placed[at];
          if (other != null && Math.abs(other.x - item.x) < other.r + item.r) {
            near.push(other);
          }
        }
      }
      const step = Math.max(1, item.r * 0.75);
      let y = 0;
      for (let tries = 0; tries < 400; tries += 1) {
        const offset = Math.ceil(tries / 2) * step * (tries % 2 === 0 ? 1 : -1);
        if (Math.abs(offset) > room) {
          y = (tries % 2 === 0 ? 1 : -1) * room;
          break;
        }
        const isClear = near.every(
          (other) =>
            (other.x - item.x) ** 2 + (other.y - offset) ** 2 >=
            (other.r + item.r) ** 2,
        );
        if (isClear) {
          y = offset;
          break;
        }
      }
      placed.push({ ...item, y });
      const list = bins.get(bin);
      if (list == null) {
        bins.set(bin, [placed.length - 1]);
      } else {
        list.push(placed.length - 1);
      }
    }
    return placed;
  }

  const swarms = $derived.by(() => {
    if (width === 0 || band === 0) {
      return [];
    }
    return lanes.map((lane, order) => {
      const middle = 14 + order * band + HEAD + (band - HEAD) / 2;
      const dots = dodge(
        places.map((row) => ({
          name: row.name,
          x: x(row.shares[lane.index] ?? 0),
          r: (1 + 5.5 * Math.sqrt(row.valid / most)) * scale,
        })),
        (band - HEAD) / 2 - 2,
      ).map((dot) => ({ ...dot, y: middle + dot.y }));
      return { lane, middle, top: 14 + order * band, dots };
    });
  });
  const paths = $derived(
    swarms.map((swarm) => {
      let all = "";
      let picked = "";
      for (const dot of swarm.dots) {
        const arc = `M${(dot.x - dot.r).toFixed(1)} ${dot.y.toFixed(1)}a${dot.r.toFixed(1)} ${dot.r.toFixed(1)} 0 1 0 ${(dot.r * 2).toFixed(1)} 0a${dot.r.toFixed(1)} ${dot.r.toFixed(1)} 0 1 0 ${(-dot.r * 2).toFixed(1)} 0`;
        all += arc;
        if (desk.picked.has(dot.name)) {
          picked += arc;
        }
      }
      return { all, picked };
    }),
  );
  const hit = $derived.by(() => {
    const name = desk.hovered;
    if (name == null) {
      return [];
    }
    return swarms.flatMap((swarm) => {
      const dot = swarm.dots.find((entry) => entry.name === name);
      return dot == null ? [] : [{ ...dot, lane: swarm.lane }];
    });
  });

  function nearest(event: PointerEvent) {
    const box = event.currentTarget;
    if (!(box instanceof HTMLElement)) {
      return;
    }
    const rect = box.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const swarm = swarms.find(
      (entry) => py >= entry.top && py < entry.top + band,
    );
    let best: { name: string; distance: number } | null = null;
    for (const dot of swarm?.dots ?? []) {
      const distance = (dot.x - px) ** 2 + (dot.y - py) ** 2;
      if (best == null || distance < best.distance) {
        best = { name: dot.name, distance };
      }
    }
    desk.hovered =
      best != null && best.distance < (24 * scale) ** 2 ? best.name : null;
  }
</script>

<div class="flex flex-col gap-1 block-full">
  <div
    class="relative flex-1 touch-none min-block-24"
    bind:clientWidth={width}
    bind:clientHeight={height}
    role="presentation"
    onpointermove={nearest}
    onpointerleave={() => (desk.hovered = null)}
    onclick={() => {
      if (desk.hovered != null) {
        desk.toggle(desk.hovered);
      }
    }}
  >
    {#if width > 0 && height > 0}
      <svg
        class="absolute inset-0"
        {width}
        {height}
        role="img"
        aria-label={m.workbench_strip_note()}
      >
        {#each TICKS as tick (tick)}
          <line
            x1={x(tick)}
            x2={x(tick)}
            y1="12"
            y2={height}
            stroke="var(--border)"
            stroke-dasharray={tick === 0.5 ? undefined : "2 3"}
          />
          <text
            x={x(tick)}
            y="9"
            text-anchor={tick === 0 ? "start" : tick === 1 ? "end" : "middle"}
            class="fill-muted-foreground font-figures text-[10px] tabular-nums"
            >{percent(tick, 0)}</text
          >
        {/each}
        {#each swarms as swarm, index (swarm.lane.key)}
          {const color = $derived(swatch(swarm.lane.color, swarm.lane.key))}
          <text
            x={PAD}
            y={swarm.top + 12}
            class="fill-foreground text-[11px] font-semibold"
            >{swarm.lane.person == null
              ? (swarm.lane.short ?? swarm.lane.label)
              : swarm.lane.label}<tspan
              class="fill-muted-foreground font-figures font-normal tabular-nums"
            >
              {percent(swarm.lane.share)}</tspan
            ></text
          >
          <line
            x1={x(swarm.lane.share)}
            x2={x(swarm.lane.share)}
            y1={swarm.top + HEAD - 2}
            y2={swarm.top + band - 2}
            stroke="var(--foreground)"
            stroke-width="1.25"
          />
          <path
            d={paths[index]?.all}
            fill={color}
            fill-opacity={desk.picked.size > 0 ? 0.18 : 0.62}
            stroke="var(--background)"
            stroke-width="0.6"
          />
          {#if (paths[index]?.picked ?? "") !== ""}
            <path
              d={paths[index]?.picked}
              fill={color}
              stroke="var(--foreground)"
              stroke-width="1"
            />
          {/if}
        {/each}
        {#each hit as dot (dot.lane.key)}
          <circle
            cx={dot.x}
            cy={dot.y}
            r={dot.r + 2}
            fill="none"
            stroke="var(--foreground)"
            stroke-width="1.5"
            class="pointer-events-none"
          />
        {/each}
      </svg>
    {/if}
  </div>
  <p class="truncate text-[0.6875em] text-muted-foreground">
    {#if desk.hovered != null && hit.length > 0}
      <span class="font-semibold text-foreground">{desk.hovered}</span>
      {#each hit as dot (dot.lane.key)}
        <span class="ms-2 font-figures tabular-nums"
          >{dot.lane.short ?? dot.lane.label}
          {percent((dot.x - PAD) / (width - PAD * 2))}</span
        >
      {/each}
    {:else}
      {m.workbench_strip_note()}
    {/if}
  </p>
</div>
