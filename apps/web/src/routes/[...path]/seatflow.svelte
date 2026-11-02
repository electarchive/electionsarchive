<script lang="ts">
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { count, dates, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    ledger,
    rows,
    path,
  }: {
    ledger: NonNullable<
      Extract<PageData, { kind: "election" }>["sections"]["ledger"]
    >;
    rows: Array<{
      key: string;
      label: string;
      short: string | null;
      color: string | null;
      share: number;
      seats: number | null;
    }>;
    path: string;
  } = $props();

  const uid = $props.id();

  const hosted = $derived(new Map(rows.map((row) => [row.key, row])));
  const parties = $derived(
    ledger.parties.map((party) => {
      const row = hosted.get(party.key);
      const label =
        party.key === "independent"
          ? m.seats_independents()
          : (row?.label ?? party.label);
      return {
        ...party,
        name: row?.short ?? party.short ?? label,
        label,
        hue: swatch(row?.color ?? party.color, party.key),
        list: row?.share ?? null,
        total:
          ledger.kind === "rounds"
            ? party.won + (party.runoff?.won ?? 0)
            : (row?.seats ?? null),
      };
    }),
  );
  const byKey = $derived(new Map(parties.map((party) => [party.key, party])));
  const rank = $derived(
    new Map(parties.map((party, index) => [party.key, index])),
  );

  const short = $derived(
    dates({ month: "short", day: "numeric", timeZone: "UTC" }),
  );
  const day = (value: string | null) =>
    value == null ? "" : short.format(new Date(`${value}T00:00:00Z`));

  let isAll = $state(false);
  const significant = $derived(
    parties.filter(
      (party) =>
        (party.total ?? 0) > 0 ||
        party.share >= 0.01 ||
        (party.list ?? 0) >= 0.01,
    ),
  );
  const listed = $derived(
    isAll || parties.length - significant.length < 2 ? parties : significant,
  );

  let width = $state(0);
  const BAR = 14;
  const SPAN = 132;
  const GAP = 2;
  let active = $state<{ from?: string; to?: string } | null>(null);

  const alluvial = $derived.by(() => {
    if (ledger.kind !== "rounds" || width === 0) {
      return null;
    }
    let total = 0;
    for (const flow of ledger.flows) {
      total += flow.count;
    }
    const order = (key: string) => rank.get(key) ?? Infinity;
    const sides = (pick: "from" | "to") => {
      const groups = Map.groupBy(ledger.flows, (flow) => flow[pick]);
      return [...groups.keys()].toSorted((a, b) => order(a) - order(b));
    };
    const tops = sides("from");
    const bottoms = sides("to");
    const scale =
      total === 0
        ? 0
        : (width - GAP * (Math.max(tops.length, bottoms.length) - 1)) / total;
    const lay = (keys: Array<string>, pick: "from" | "to") => {
      let x = 0;
      return keys.map((key) => {
        let size = 0;
        for (const flow of ledger.flows) {
          if (flow[pick] === key) {
            size += flow.count;
          }
        }
        const segment = { key, x, size, width: size * scale };
        x += segment.width + GAP;
        return segment;
      });
    };
    const top = lay(tops, "from");
    const bottom = lay(bottoms, "to");
    const cursorTop = new Map(top.map((segment) => [segment.key, segment.x]));
    const cursorBottom = new Map(
      bottom.map((segment) => [segment.key, segment.x]),
    );
    const outgoing = ledger.flows.toSorted(
      (a, b) =>
        order(a.from) - order(b.from) ||
        Number(a.isRunoff) - Number(b.isRunoff) ||
        order(a.to) - order(b.to),
    );
    const placed = new Map<
      (typeof ledger.flows)[number],
      { x0: number; x1: number }
    >();
    for (const flow of outgoing) {
      const x0 = cursorTop.get(flow.from) ?? 0;
      cursorTop.set(flow.from, x0 + flow.count * scale);
      placed.set(flow, { x0, x1: 0 });
    }
    const incoming = ledger.flows.toSorted(
      (a, b) =>
        order(a.to) - order(b.to) ||
        Number(a.isRunoff) - Number(b.isRunoff) ||
        order(a.from) - order(b.from),
    );
    for (const flow of incoming) {
      const x1 = cursorBottom.get(flow.to) ?? 0;
      cursorBottom.set(flow.to, x1 + flow.count * scale);
      const spot = placed.get(flow);
      if (spot != null) {
        spot.x1 = x1;
      }
    }
    const y0 = BAR + 36;
    const y1 = y0 + SPAN;
    const middle = (y0 + y1) / 2;
    const ribbons = ledger.flows.map((flow, index) => {
      const spot = placed.get(flow) ?? { x0: 0, x1: 0 };
      const w = flow.count * scale;
      return {
        ...flow,
        index,
        d: `M${spot.x0},${y0}C${spot.x0},${middle} ${spot.x1},${middle} ${spot.x1},${y1}L${spot.x1 + w},${y1}C${spot.x1 + w},${middle} ${spot.x0 + w},${middle} ${spot.x0 + w},${y0}Z`,
      };
    });
    return { top, bottom, ribbons, y0, y1, height: y1 + BAR + 20 };
  });

  const isLit = (flow: { from: string; to: string }) =>
    active == null ||
    ((active.from == null || active.from === flow.from) &&
      (active.to == null || active.to === flow.to));

  const readout = $derived.by(() => {
    if (active == null || ledger.kind !== "rounds") {
      return null;
    }
    const picked = ledger.flows.filter(isLit);
    let total = 0;
    let runoffs = 0;
    for (const flow of picked) {
      total += flow.count;
      runoffs += flow.isRunoff ? flow.count : 0;
    }
    const from = active.from == null ? null : byKey.get(active.from);
    const to = active.to == null ? null : byKey.get(active.to);
    return {
      from: from?.name ?? null,
      to: to?.name ?? null,
      total,
      runoffs,
      outright: total - runoffs,
    };
  });

  const fields = $derived(
    [...Map.groupBy(ledger.matchups, (matchup) => matchup.keys.length)]
      .map(([size, matchups]) => {
        let total = 0;
        for (const matchup of matchups) {
          total += matchup.count;
        }
        return { size, matchups, total };
      })
      .toSorted((a, b) => b.total - a.total),
  );
  const widest = $derived(
    Math.max(1, ...ledger.matchups.map((matchup) => matchup.count)),
  );
  const opened = $state<Record<number, boolean>>({});

  const seatsTop = $derived(
    Math.max(1, ...parties.map((party) => party.total ?? 0)),
  );
  let isUnseatedAll = $state(false);
  const unseated = $derived(
    isUnseatedAll ? ledger.unseated : ledger.unseated.slice(0, 8),
  );
  const totals = $derived.by(() => {
    let seated = 0;
    let seats = 0;
    for (const party of parties) {
      seated += party.seated;
      seats += party.total ?? 0;
    }
    return { seated, seats };
  });

  const isHere = (target: string | null) =>
    target != null && path.replace(/^\/+/u, "") === target;
</script>

{#snippet chip(key: string)}
  {const party = $derived(byKey.get(key))}
  <span class="inline-flex items-center gap-1 whitespace-nowrap">
    <span
      class="shrink-0 rounded-xs block-2.5 inline-2.5"
      style:background={party?.hue ?? swatch(null, key)}
    ></span>
    <span class="font-medium">{party?.name ?? ""}</span>
  </span>
{/snippet}

{#snippet more(isOpen: boolean, hidden: number, toggle: () => void)}
  <button
    type="button"
    class="inline-flex items-center gap-1 justify-self-start text-[0.8125rem] text-muted-foreground hover:text-foreground"
    aria-expanded={isOpen}
    onclick={toggle}
  >
    {isOpen ? m.results_fewer() : m.places_show_more({ count: count(hidden) })}
    <ChevronDownIcon
      class={cn("transition-transform block-3.5 inline-3.5", {
        "rotate-180": isOpen,
      })}
      aria-hidden="true"
    />
  </button>
{/snippet}

<section
  class="col-span-12 grid gap-4 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="{uid}-title"
>
  <div class="grid gap-1.5">
    <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
      <h2
        id="{uid}-title"
        class="text-2xl/tight font-[760] tracking-[-0.015em] text-balance"
      >
        {ledger.kind === "rounds"
          ? m.seatflow_rounds_title()
          : m.seatflow_votes_title()}
      </h2>
      {#if ledger.kind === "rounds"}
        <nav
          class="flex flex-wrap items-baseline gap-x-4 gap-y-1.5 text-[0.8125rem]"
          aria-label={m.rounds_title()}
        >
          {#if ledger.path != null && !isHere(ledger.path) && ledger.rounds.every((round) => round.path !== ledger.path)}
            <Preview
              class="font-medium underline decoration-border underline-offset-4 hover:decoration-foreground"
              path={ledger.path}>{m.seatflow_both()}</Preview
            >
          {/if}
          {#each ledger.rounds as round, at (round.id)}
            {const label = $derived(
              at === 0
                ? m.round_first()
                : at === 1
                  ? m.round_second()
                  : m.round_number({ round: at + 1 }),
            )}
            <span class="inline-flex items-baseline gap-1.5">
              {#if round.path == null || isHere(round.path)}
                <span
                  class="font-semibold"
                  aria-current={round.path != null ? "page" : undefined}
                  >{label}</span
                >
              {:else}
                <Preview
                  class="font-medium underline decoration-border underline-offset-4 hover:decoration-foreground"
                  path={round.path}>{label}</Preview
                >
              {/if}
              <span class="font-figures text-muted-foreground tabular-nums"
                >{day(round.date)}</span
              >
            </span>
          {/each}
        </nav>
      {/if}
    </div>
    <p class="text-[0.9375rem] text-pretty max-inline-[68ch]">
      {#if ledger.kind === "rounds"}
        {m.seatflow_rounds_lede({
          decided: count(ledger.decided),
          size: count(ledger.size),
          runoffs: count(ledger.runoffs),
        })}
        {#if ledger.comebacks > 0}
          {m.seatflow_rounds_comebacks({ count: ledger.comebacks })}
        {/if}
      {:else}
        {m.seatflow_votes_lede({
          seated: count(totals.seated),
          seats: count(totals.seats),
          list: count(Math.max(0, totals.seats - totals.seated)),
        })}
        {#if ledger.unseated.length > 0}
          {m.seatflow_votes_unseated({ count: ledger.unseated.length })}
        {/if}
      {/if}
    </p>
  </div>

  {#if ledger.kind === "rounds"}
    <figure class="grid gap-2" bind:clientWidth={width}>
      <figcaption
        class="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[0.8125rem] text-muted-foreground min-block-5"
        aria-live="polite"
      >
        {#if readout != null}
          <span class="text-foreground">
            {readout.from != null && readout.to != null
              ? m.seatflow_flow_pair({ from: readout.from, to: readout.to })
              : readout.from != null
                ? m.seatflow_flow_from({ from: readout.from })
                : m.seatflow_flow_to({ to: readout.to ?? "" })}
          </span>
          <span class="font-figures tabular-nums"
            >{m.seatflow_flow_count({
              outright: count(readout.outright),
              runoffs: count(readout.runoffs),
            })}</span
          >
        {:else}
          <span class="inline-flex items-center gap-1.5">
            <span class="bg-foreground/85 block-2.5 inline-4"></span>
            {m.seatflow_flow_outright()}
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="bg-foreground/30 block-2.5 inline-4"></span>
            {m.seatflow_flow_runoff()}
          </span>
        {/if}
      </figcaption>
      {#if alluvial != null}
        <svg
          class="block overflow-visible inline-full"
          viewBox="0 0 {width} {alluvial.height}"
          height={alluvial.height}
          role="img"
          aria-labelledby="{uid}-title"
          onpointerleave={() => (active = null)}
        >
          <text x="0" y="10" class="fill-muted-foreground text-[0.75rem]"
            >{m.seatflow_flow_top()}</text
          >
          {#each alluvial.top as segment (segment.key)}
            {const party = $derived(byKey.get(segment.key))}
            <g
              role="presentation"
              onpointerenter={() => (active = { from: segment.key })}
            >
              <rect
                x={segment.x}
                y={alluvial.y0 - BAR}
                width={Math.max(segment.width, 0.5)}
                height={BAR}
                fill={party?.hue ?? swatch(null, segment.key)}
                class={cn("transition-opacity", {
                  "opacity-30": active != null && active.from !== segment.key,
                })}
              />
              {#if segment.width >= 44}
                <text
                  x={segment.x + 3}
                  y={alluvial.y0 - BAR - 4}
                  class="fill-foreground font-figures text-[0.75rem] font-medium tabular-nums"
                  >{party?.name} {count(segment.size)}</text
                >
              {/if}
            </g>
          {/each}
          {#each alluvial.ribbons as ribbon (ribbon.index)}
            <path
              d={ribbon.d}
              fill={byKey.get(ribbon.to)?.hue ?? swatch(null, ribbon.to)}
              class="transition-opacity"
              opacity={isLit(ribbon) ? (ribbon.isRunoff ? 0.42 : 0.88) : 0.08}
              role="presentation"
              onpointerenter={() =>
                (active = { from: ribbon.from, to: ribbon.to })}
            />
          {/each}
          {#each alluvial.bottom as segment (segment.key)}
            {const party = $derived(byKey.get(segment.key))}
            <g
              role="presentation"
              onpointerenter={() => (active = { to: segment.key })}
            >
              <rect
                x={segment.x}
                y={alluvial.y1}
                width={Math.max(segment.width, 0.5)}
                height={BAR}
                fill={party?.hue ?? swatch(null, segment.key)}
                class={cn("transition-opacity", {
                  "opacity-30": active != null && active.to !== segment.key,
                })}
              />
              {#if segment.width >= 44}
                <text
                  x={segment.x + 3}
                  y={alluvial.y1 + BAR + 13}
                  class="fill-foreground font-figures text-[0.75rem] font-medium tabular-nums"
                  >{party?.name} {count(segment.size)}</text
                >
              {/if}
            </g>
          {/each}
          <text
            x={width}
            y={alluvial.height - 1}
            text-anchor="end"
            class="fill-muted-foreground text-[0.75rem]"
            >{m.seatflow_flow_bottom()}</text
          >
        </svg>
      {/if}
    </figure>
  {/if}

  <div class="-mx-3.5 overflow-x-auto">
    <table class="border-collapse text-sm inline-full">
      <thead class="text-[0.8125rem] text-muted-foreground">
        {#if ledger.kind === "rounds"}
          <tr>
            <th class="sticky inset-s-0 bg-card ps-3.5" scope="col"></th>
            <th
              class="border-be border-border px-2 pbe-1 text-start font-semibold text-foreground"
              colspan="5"
              scope="colgroup"
            >
              {m.round_first()}
              <span class="font-figures font-normal text-muted-foreground"
                >{day(ledger.rounds[0]?.date ?? null)}</span
              >
            </th>
            <th
              class="border-be border-border px-2 pbe-1 text-start font-semibold text-foreground"
              colspan="3"
              scope="colgroup"
            >
              {m.round_second()}
              <span class="font-figures font-normal text-muted-foreground"
                >{day(ledger.rounds[1]?.date ?? null)}</span
              >
            </th>
            <th class="pe-3.5" scope="col"></th>
          </tr>
        {/if}
        <tr>
          <th
            class="sticky inset-s-0 bg-card ps-3.5 pe-3 pbs-1.5 pbe-1.5 text-start font-normal"
            scope="col">{m.col_party()}</th
          >
          {#if ledger.kind === "rounds"}
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.col_share()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_led()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_outright()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_advanced()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_withdrew()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.col_share()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_won()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_behind()}</th
            >
          {:else}
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_constituency_vote()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.seatflow_col_list_vote()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.col_constituency_seats()}</th
            >
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col"
              >{m.col_list_seats()}</th
            >
          {/if}
          <th
            class="ps-2 pe-3.5 pbe-1.5 text-start font-normal min-inline-40"
            scope="col">{m.col_seats()}</th
          >
        </tr>
      </thead>
      <tbody class="font-figures whitespace-nowrap tabular-nums">
        {#each listed as party (party.key)}
          {const outright = $derived(
            ledger.kind === "rounds" ? party.won : party.seated,
          )}
          {const later = $derived(
            ledger.kind === "rounds"
              ? (party.runoff?.won ?? 0)
              : Math.max(0, (party.total ?? 0) - party.seated),
          )}
          <tr class="border-bs border-border/60">
            <th
              class="sticky inset-s-0 bg-card py-1.5 ps-3.5 pe-3 text-start font-sans font-semibold whitespace-nowrap"
              scope="row"
            >
              <span class="inline-flex items-center gap-1.5">
                <span
                  class="shrink-0 rounded-xs block-2.5 inline-2.5"
                  style:background={party.hue}
                ></span>
                <span title={party.label}>{party.name}</span>
              </span>
            </th>
            {#if ledger.kind === "rounds"}
              <td class="px-2 text-end">{percent(party.share)}</td>
              <td class="px-2 text-end">{party.led || "–"}</td>
              <td class="px-2 text-end">{party.won || "–"}</td>
              <td class="px-2 text-end">{party.advanced || "–"}</td>
              <td class="px-2 text-end text-muted-foreground"
                >{party.withdrew || "–"}</td
              >
              <td class="px-2 text-end"
                >{party.runoff == null || party.runoff.stood === 0
                  ? "–"
                  : percent(party.runoff.share)}</td
              >
              <td class="px-2 text-end"
                >{party.runoff == null || party.runoff.stood === 0
                  ? "–"
                  : m.seatflow_won_of({
                      won: count(party.runoff.won),
                      stood: count(party.runoff.stood),
                    })}</td
              >
              <td class="px-2 text-end"
                >{(party.runoff?.comebacks ?? 0) > 0
                  ? party.runoff?.comebacks
                  : "–"}</td
              >
            {:else}
              <td class="px-2 text-end">{percent(party.share)}</td>
              <td class="px-2 text-end"
                >{party.list == null ? "–" : percent(party.list)}</td
              >
              <td class="px-2 text-end">
                {party.won || "–"}
                {#if party.won > party.seated}
                  <span class="block text-xs text-muted-foreground"
                    >{m.seatflow_unseated_count({
                      count: count(party.won - party.seated),
                    })}</span
                  >
                {/if}
              </td>
              <td class="px-2 text-end"
                >{party.total == null ? "–" : later || "–"}</td
              >
            {/if}
            <td class="ps-2 pe-3.5">
              <span class="flex items-center gap-2">
                <span class="text-end font-semibold min-inline-[3ch]"
                  >{party.total ?? "–"}</span
                >
                <span
                  class="flex block-2.5 inline-full max-inline-48"
                  aria-hidden="true"
                >
                  <span
                    style:background={party.hue}
                    style:inline-size="{(outright / seatsTop) * 100}%"
                  ></span>
                  <span
                    class="opacity-45"
                    style:background={party.hue}
                    style:inline-size="{(later / seatsTop) * 100}%"
                  ></span>
                </span>
              </span>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if parties.length - significant.length >= 2}
    {@render more(isAll, parties.length - significant.length, () => {
      isAll = !isAll;
    })}
  {/if}
  <p class="-mbs-2 text-xs text-muted-foreground">
    {ledger.kind === "rounds"
      ? m.seatflow_bar_rounds()
      : m.seatflow_bar_votes()}
  </p>

  {#if ledger.kind === "rounds" && fields.length > 0}
    <div class="grid gap-3">
      <h3 class="text-base font-bold">{m.seatflow_matchups_title()}</h3>
      <div class="grid gap-x-8 gap-y-4 md:grid-cols-2">
        {#each fields as field (field.size)}
          {const isOpen = $derived(opened[field.size] === true)}
          {const shown = $derived(
            isOpen ? field.matchups : field.matchups.slice(0, 6),
          )}
          <div class="grid content-start gap-1.5">
            <h4
              class="flex items-baseline justify-between gap-3 border-be border-border pbe-1 text-[0.8125rem] font-semibold"
            >
              {field.size === 1
                ? m.seatflow_field_one()
                : m.seatflow_field({ size: count(field.size) })}
              <span
                class="font-figures font-normal text-muted-foreground tabular-nums"
                >{count(field.total)}</span
              >
            </h4>
            <ul class="grid gap-1.5 text-sm">
              {#each shown as matchup (matchup.keys.join(" "))}
                <li
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1"
                >
                  <span class="flex flex-wrap gap-x-2.5 gap-y-0.5">
                    {#each matchup.keys as key, at (at)}
                      {@render chip(key)}
                    {/each}
                  </span>
                  <span class="font-figures text-muted-foreground tabular-nums"
                    >{count(matchup.count)}</span
                  >
                  <span
                    class="col-span-full flex block-1.5"
                    style:inline-size="{(matchup.count / widest) * 100}%"
                    aria-hidden="true"
                  >
                    {#each matchup.wins as win (win.key)}
                      <span
                        class="first:rounded-s-xs last:rounded-e-xs"
                        style:background={byKey.get(win.key)?.hue ??
                          swatch(null, win.key)}
                        style:flex-grow={win.count}
                        title={m.seatflow_matchup_wins({
                          party: byKey.get(win.key)?.name ?? "",
                          count: count(win.count),
                        })}
                      ></span>
                    {/each}
                  </span>
                  {#if matchup.wins.length > 0}
                    <span
                      class="col-span-full -mbs-0.5 text-xs text-muted-foreground"
                      >{matchup.wins
                        .map((win) =>
                          m.seatflow_matchup_wins({
                            party: byKey.get(win.key)?.name ?? "",
                            count: count(win.count),
                          }),
                        )
                        .join(", ")}</span
                    >
                  {/if}
                </li>
              {/each}
            </ul>
            {#if field.matchups.length > 6}
              {@render more(isOpen, field.matchups.length - 6, () => {
                opened[field.size] = !isOpen;
              })}
            {/if}
          </div>
        {/each}
      </div>
      {#if ledger.withdrew > 0}
        <div class="grid gap-1 text-[0.8125rem] text-muted-foreground">
          <p class="text-foreground">
            {m.seatflow_withdrew({ count: ledger.withdrew })}
          </p>
          <ul class="grid gap-0.5">
            {#each ledger.shrunk.filter((step) => step.from > step.to) as step (`${step.from}:${step.to}`)}
              <li class="font-figures tabular-nums">
                {m.seatflow_shrunk({
                  count: step.count,
                  from: count(step.from),
                  to: count(step.to),
                })}
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </div>
  {/if}

  {#if ledger.unseated.length > 0}
    <div class="grid gap-2">
      <h3 class="text-base font-bold">
        {m.seatflow_unseated_title()}
        <span
          class="font-figures font-normal text-muted-foreground tabular-nums"
          >{count(ledger.unseated.length)}</span
        >
      </h3>
      <div class="-mx-3.5 overflow-x-auto">
        <table class="border-collapse text-sm inline-full">
          <thead class="text-[0.8125rem] text-muted-foreground">
            <tr>
              <th class="ps-3.5 pe-3 pbe-1.5 text-start font-normal" scope="col"
                >{m.col_name()}</th
              >
              <th class="px-3 pbe-1.5 text-start font-normal" scope="col"
                >{m.seatflow_col_constituency()}</th
              >
              <th class="px-3 pbe-1.5 text-start font-normal" scope="col"
                >{m.col_party()}</th
              >
              <th class="ps-3 pe-3.5 pbe-1.5 text-end font-normal" scope="col"
                >{m.col_share()}</th
              >
            </tr>
          </thead>
          <tbody>
            {#each unseated as row (row.person)}
              <tr class="border-bs border-border/60">
                <td class="py-1.5 ps-3.5 pe-3 font-medium whitespace-nowrap"
                  >{row.name}</td
                >
                <td class="px-3 whitespace-nowrap">
                  {#if row.path == null}
                    {row.place}
                  {:else}
                    <Preview
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      path={row.path}>{row.place}</Preview
                    >
                  {/if}
                </td>
                <td class="px-3">{@render chip(row.key)}</td>
                <td class="ps-3 pe-3.5 text-end font-figures tabular-nums"
                  >{percent(row.share)}</td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if ledger.unseated.length > 8}
        {@render more(isUnseatedAll, ledger.unseated.length - 8, () => {
          isUnseatedAll = !isUnseatedAll;
        })}
      {/if}
    </div>
  {/if}
</section>
