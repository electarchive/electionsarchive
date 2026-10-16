<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Export from "#lib/components/export.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  let {
    terms,
    parties,
    governments,
    basis,
    selected = $bindable(),
  }: {
    terms: Array<{
      start: string;
      end: string | null;
      total: number;
      size: number | null;
      parties: Array<{ party: string | null; seats: number }>;
      election: { name: string; path: string | null; date: string } | null;
    }>;
    parties: Array<{
      id: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
    }>;
    governments: Array<{
      name: string;
      head: string | null;
      party: string | null;
      from: string;
      to: string | null;
      isCaretaker: boolean;
    }>;
    basis: "seated" | "mixed" | "results";
    selected: number;
  } = $props();

  const uid = $props.id();
  const PAD = { top: 10, side: 1, axis: 22 };

  let width = $state(960);
  let isShare = $state(page.url.searchParams.get("scale") === "share");

  $effect(() => {
    keep({ scale: isShare ? "share" : null });
  });
  let hovered = $state<number | null>(null);
  let seated = $state<string | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const info = $derived(new Map(parties.map((party) => [party.id, party])));
  const tint = (party: string | null) =>
    party == null
      ? swatch(null, "")
      : swatch(info.get(party)?.color ?? null, `party:${party}`);
  const label = (party: string | null) =>
    party == null
      ? m.record_none()
      : (info.get(party)?.short ?? info.get(party)?.label ?? "");
  const band = $derived(governments.length > 0 ? 18 : 0);
  const height = $derived(width < 640 ? 230 : 320);
  const inner = $derived(height - PAD.top - PAD.axis - band);
  const low = $derived(Date.parse(terms[0]?.start ?? today));
  const high = $derived(
    Math.max(Date.parse(today), Date.parse(terms.at(-1)?.start ?? today) + 1),
  );
  const sx = (value: number) =>
    PAD.side +
    ((value - low) / Math.max(high - low, 1)) * (width - 2 * PAD.side);
  const peak = $derived(
    Math.max(1, ...terms.map((term) => Math.max(term.total, term.size ?? 0))),
  );
  const order = $derived.by(() => {
    const held = new Map(
      Map.groupBy(
        terms.flatMap((term) => term.parties),
        (entry) => entry.party,
      )
        .entries()
        .map(([party, entries]) => {
          let sum = 0;
          for (const entry of entries) {
            sum += entry.seats;
          }
          return [party, sum] as const;
        }),
    );
    return [...held.keys()].toSorted(
      (a, b) =>
        Number(a == null) - Number(b == null) ||
        (info.get(a ?? "")?.lean ?? Infinity) -
          (info.get(b ?? "")?.lean ?? Infinity) ||
        (held.get(b) ?? 0) - (held.get(a) ?? 0),
    );
  });
  const columns = $derived(
    terms.map((term, index) => {
      const x0 = sx(Date.parse(term.start));
      const x1 = sx(Date.parse(term.end ?? today));
      const whole = Math.max(term.total, 1);
      const unit = isShare ? inner / whole : inner / peak;
      const seats = new Map(
        term.parties.map((entry) => [entry.party, entry.seats]),
      );
      let below = 0;
      const layers: Array<[string | null, { y0: number; y1: number }]> = [];
      for (const party of order) {
        const value = seats.get(party) ?? 0;
        layers.push([
          party,
          {
            y0: PAD.top + inner - below * unit,
            y1: PAD.top + inner - (below + value) * unit,
          },
        ]);
        below += value;
      }
      const n = term.size ?? term.total;
      const majority = Math.floor(n / 2) + 1;
      return {
        index,
        term,
        x0,
        x1,
        stack: new Map(layers),
        line:
          PAD.top +
          inner -
          (isShare ? (majority / Math.max(n, 1)) * inner : majority * unit),
      };
    }),
  );
  const bands = $derived(
    order.map((party) => {
      const runs = columns.map((column, index) => {
        const ease = Math.min(5, (column.x1 - column.x0) * 0.22);
        const joinsBefore =
          index > 0 && column.x0 - (columns[index - 1]?.x1 ?? 0) < 1;
        const joinsAfter =
          index < columns.length - 1 &&
          (columns[index + 1]?.x0 ?? 0) - column.x1 < 1;
        return {
          a: column.x0 + (joinsBefore ? ease : 0),
          b: column.x1 - (joinsAfter ? ease : 0),
          edge: column.x1,
          joinsAfter,
          ...(column.stack.get(party) ?? { y0: 0, y1: 0 }),
        };
      });
      const groups: Array<typeof runs> = [];
      for (const run of runs) {
        const group = groups.at(-1);
        if (group == null || group.at(-1)?.joinsAfter !== true) {
          groups.push([run]);
        } else {
          group.push(run);
        }
      }
      let d = "";
      for (const group of groups) {
        for (const [index, run] of group.entries()) {
          const before = group[index - 1];
          d +=
            before == null
              ? `M${run.a},${run.y1}`
              : `C${before.edge},${before.y1} ${before.edge},${run.y1} ${run.a},${run.y1}`;
          d += `L${run.b},${run.y1}`;
        }
        for (const [index, run] of group.entries().toArray().toReversed()) {
          const after = group[index + 1];
          d +=
            after == null
              ? `L${run.b},${run.y0}`
              : `C${run.edge},${after.y0} ${run.edge},${run.y0} ${run.b},${run.y0}`;
          d += `L${run.a},${run.y0}`;
        }
        d += "Z";
      }
      return { party, d: d.replaceAll(/(\.\d)\d+/g, "$1") };
    }),
  );
  const majority = $derived.by(() => {
    let d = "";
    for (const [index, column] of columns.entries()) {
      const isJoined =
        index > 0 && column.x0 - (columns[index - 1]?.x1 ?? 0) < 1;
      d += `${isJoined ? "L" : "M"}${column.x0},${column.line}L${column.x1},${column.line}`;
    }
    return d;
  });
  const names = $derived.by(() => {
    const placed: Array<{ party: string; x: number; y: number }> = [];
    for (const party of order) {
      if (party == null) {
        continue;
      }
      const text = label(party);
      let best: { x: number; y: number; room: number } | null = null;
      for (const column of columns) {
        const at = column.stack.get(party);
        const thick = at == null ? 0 : at.y0 - at.y1;
        const wide = column.x1 - column.x0;
        if (at != null && thick >= 15 && wide >= text.length * 6.6 + 12) {
          const room = thick * Math.min(wide, 220);
          if (best == null || room > best.room) {
            best = {
              x: (column.x0 + column.x1) / 2,
              y: (at.y0 + at.y1) / 2,
              room,
            };
          }
        }
      }
      if (best != null) {
        placed.push({ party, x: best.x, y: best.y });
      }
    }
    return placed;
  });
  const years = $derived.by(() => {
    const first = new Date(low).getUTCFullYear();
    const last = new Date(high).getUTCFullYear();
    const every =
      [1, 2, 5, 10, 20, 25, 50].find(
        (size) => ((last - first) / size) * 48 < width,
      ) ?? 50;
    const found: Array<{ year: number; x: number }> = [];
    for (
      let year = Math.ceil((first + 1) / every) * every;
      year <= last;
      year += every
    ) {
      found.push({ year, x: sx(Date.UTC(year, 0, 1)) });
    }
    return found;
  });
  const cabinets = $derived(
    governments.flatMap((cabinet) => {
      const from = Math.max(low, Date.parse(cabinet.from));
      const to = Date.parse(cabinet.to ?? today);
      return to <= from
        ? []
        : [
            {
              ...cabinet,
              x: sx(from),
              w: Math.max(sx(to) - sx(from) - 0.5, 0.6),
            },
          ];
    }),
  );
  const lit = $derived(columns[hovered ?? -1] ?? null);
  const sitting = $derived(
    cabinets.find((cabinet) => cabinet.from === seated) ?? null,
  );
  const legend = $derived(
    order
      .filter((party) =>
        terms.some((term) =>
          term.parties.some(
            (entry) =>
              entry.party === party && entry.seats >= 0.06 * term.total,
          ),
        ),
      )
      .filter(
        (party, index, all) =>
          all.findIndex((other) => label(other) === label(party)) === index,
      )
      .slice(0, 14),
  );

  const locate = (event: MouseEvent & { currentTarget: SVGSVGElement }) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * width;
    const found = columns.findIndex(
      (column) => x >= column.x0 && x < column.x1,
    );
    return found < 0
      ? x < (columns[0]?.x0 ?? 0)
        ? 0
        : columns.length - 1
      : found;
  };
</script>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-labelledby="{uid}-title"
  data-export
>
  <Export name="makeup" class="absolute inset-e-2 inset-bs-2" />
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
    <div class="grid gap-0.5">
      <h2
        id="{uid}-title"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.chamber_makeup_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[68ch]">
        {basis === "seated"
          ? m.chamber_makeup_seated()
          : basis === "mixed"
            ? m.chamber_makeup_mixed()
            : m.chamber_makeup_results()}
      </p>
    </div>
    <div class="flex" role="group" aria-label={m.chamber_scale()}>
      {#each [false, true] as share (share)}
        <button
          type="button"
          class={cn(
            "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
            {
              "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                isShare === share,
            },
          )}
          aria-pressed={isShare === share}
          onclick={() => (isShare = share)}
          >{share ? m.chamber_scale_share() : m.chamber_scale_seats()}</button
        >
      {/each}
    </div>
  </div>
  <div class="relative min-inline-0" bind:clientWidth={width}>
    <svg
      class="block cursor-pointer touch-pan-y overflow-visible rounded-sm outline-none block-auto max-inline-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
      viewBox="0 0 {width} {height}"
      {width}
      {height}
      role="slider"
      tabindex="0"
      aria-label={m.chamber_term_pick()}
      aria-valuemin={0}
      aria-valuemax={Math.max(columns.length - 1, 0)}
      aria-valuenow={selected}
      aria-valuetext={terms[selected]?.election?.name ??
        terms[selected]?.start ??
        ""}
      onpointermove={(event) => (hovered = locate(event))}
      onpointerleave={() => (hovered = null)}
      onclick={(event) => (selected = locate(event))}
      onkeydown={(event) => {
        const moves: Record<string, number> = {
          ArrowRight: 1,
          ArrowUp: 1,
          ArrowLeft: -1,
          ArrowDown: -1,
          Home: -Infinity,
          End: Infinity,
        };
        const move = moves[event.key];
        if (move != null) {
          event.preventDefault();
          selected = Math.min(columns.length - 1, Math.max(0, selected + move));
        }
      }}
    >
      <g>
        {#each bands as entry (entry.party ?? "none")}
          <path
            d={entry.d}
            fill={tint(entry.party)}
            stroke="var(--card)"
            stroke-width="0.5"
            stroke-linejoin="round"
          />
        {/each}
      </g>
      {#if lit != null}
        <path
          d="M{PAD.side},{PAD.top}H{lit.x0}V{PAD.top +
            inner}H{PAD.side}ZM{lit.x1},{PAD.top}H{width}V{PAD.top +
            inner}H{lit.x1}Z"
          class="pointer-events-none fill-card"
          opacity="0.5"
        />
      {/if}
      {#each names as name (name.party)}
        <text
          x={name.x}
          y={name.y}
          dy="0.35em"
          text-anchor="middle"
          class="pointer-events-none text-[0.6875rem] font-semibold"
          style:fill={`color(from ${tint(name.party)} xyz-d65 calc(0.9505 * clamp(0, (0.179 - y) * 1e6, 1)) clamp(0, (0.179 - y) * 1e6, 1) calc(1.089 * clamp(0, (0.179 - y) * 1e6, 1)))`}
          >{label(name.party)}</text
        >
      {/each}
      <path
        d={majority}
        fill="none"
        class="pointer-events-none stroke-card"
        stroke-width="3"
        stroke-opacity="0.6"
      />
      <path
        d={majority}
        fill="none"
        class="pointer-events-none stroke-foreground"
        stroke-width="1.25"
        stroke-dasharray="4 3"
      />
      {#each [columns[selected]] as column (column?.index)}
        {#if column != null}
          <rect
            x={column.x0}
            y={PAD.top - 4}
            width={Math.max(column.x1 - column.x0, 1.5)}
            height={inner + 4}
            class="pointer-events-none fill-none stroke-foreground"
            stroke-width="1.5"
          />
          <path
            d="M{(column.x0 + column.x1) / 2 - 5},{PAD.top - 10}l5,6l5,-6z"
            class="pointer-events-none fill-foreground"
          />
        {/if}
      {/each}
      {#each cabinets as cabinet (cabinet.from)}
        <rect
          x={cabinet.x}
          y={PAD.top + inner + 6}
          width={cabinet.w}
          height="9"
          fill={tint(cabinet.party)}
          opacity={seated == null || seated === cabinet.from
            ? cabinet.isCaretaker
              ? 0.5
              : 1
            : 0.3}
          role="presentation"
          onpointerenter={() => (seated = cabinet.from)}
          onpointerleave={() => (seated = null)}
        />
      {/each}
      {#each years as year (year.year)}
        <line
          x1={year.x}
          x2={year.x}
          y1={height - PAD.axis + 2}
          y2={height - PAD.axis + 6}
          class="stroke-muted-foreground"
        />
        <text
          x={year.x}
          y={height - 4}
          text-anchor="middle"
          class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
          >{year.year}</text
        >
      {/each}
    </svg>
    {#if lit != null}
      {const leader = $derived(lit.term.parties[0])}
      <div
        class="pointer-events-none absolute grid gap-1 rounded-sm bg-card/95 px-2.5 py-2 text-xs shadow-menu ring-1 ring-border inline-60"
        style:inset-inline-start="{lit.x1 + 248 > width
          ? Math.max(0, lit.x0 - 248)
          : lit.x1 + 8}px"
        style:inset-block-start="{PAD.top}px"
      >
        <span class="text-[0.8125rem] font-semibold"
          >{lit.term.election?.name ?? date(lit.term.start)}</span
        >
        <span class="font-mono text-muted-foreground tabular-nums"
          >{date(lit.term.start)} – {lit.term.end == null
            ? m.governments_present()
            : date(lit.term.end)}</span
        >
        <ul class="grid gap-0.5">
          {#each lit.term.parties.slice(0, 6) as entry (entry.party ?? "none")}
            <li class="flex items-center justify-between gap-3">
              <span class="flex items-center gap-1.5 truncate">
                <span
                  class="shrink-0 rounded-xs block-2.5 inline-2.5"
                  style:background={tint(entry.party)}
                ></span>
                <span class="truncate">{label(entry.party)}</span>
              </span>
              <span class="font-mono tabular-nums"
                ><span class="font-semibold">{count(entry.seats)}</span>
                <span class="text-muted-foreground"
                  >{percent(entry.seats / Math.max(lit.term.total, 1))}</span
                ></span
              >
            </li>
          {/each}
        </ul>
        {#if leader != null}
          <span class="text-muted-foreground"
            >{m.record_seats({
              seats: count(leader.seats),
              total: count(lit.term.size ?? lit.term.total),
            })}</span
          >
        {/if}
      </div>
    {/if}
  </div>
  {#if cabinets.length > 0}
    <p
      class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.8125rem] min-block-5"
      aria-live="polite"
    >
      {#if sitting == null}
        <span class="font-semibold">{m.record_governments()}</span>
        <span class="text-muted-foreground">{m.record_governments_key()}</span>
      {:else}
        <span class="flex items-center gap-1.5 font-semibold">
          <span
            class="rounded-xs block-2.5 inline-2.5"
            style:background={tint(sitting.party)}
          ></span>
          {sitting.name}
        </span>
        {#if sitting.head != null}
          <span>{m.record_governments_head({ name: sitting.head })}</span>
        {/if}
        <span class="font-mono text-xs text-muted-foreground tabular-nums"
          >{date(sitting.from)} – {sitting.to == null
            ? m.governments_present()
            : date(sitting.to)}</span
        >
      {/if}
    </p>
  {/if}
  <ul class="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
    {#each legend as party (party ?? "none")}
      <li class="flex items-center gap-1.5">
        <span
          class="rounded-xs block-2.5 inline-2.5"
          style:background={tint(party)}
        ></span>
        {#if party == null}
          <span class="text-muted-foreground">{label(party)}</span>
        {:else}
          <a class="hover:underline" href={resolve("/id/[id]", { id: party })}
            >{label(party)}</a
          >
        {/if}
      </li>
    {/each}
  </ul>
</section>
