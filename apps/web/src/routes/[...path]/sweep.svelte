<script lang="ts">
  import type { ComponentProps } from "svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import Name from "#lib/components/name.svelte";
  import { withEntries, withLegend } from "#lib/crowned.js";
  import { capitalize, count, percent, plural, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type PlaceMap from "./map.svelte";
  import Crowns from "./crowns.svelte";
  import Preview from "./preview.svelte";

  const {
    id,
    year,
    hexes,
  }: {
    id: string;
    year: string;
    hexes: ComponentProps<typeof PlaceMap>["hexes"];
  } = $props();

  const uid = $props.id();
  const ORDER = ["metro", "seat", "district", "town"] as const;
  const TITLES = {
    metro: m.sweep_tier_metro,
    seat: m.sweep_tier_seat,
    district: m.sweep_tier_district,
    town: m.sweep_tier_town,
  };
  let focus = $state<number | null>(null);
  let isAll = $state(false);

  const winners = createQuery(() => ({
    ...orpc.elections.winners.queryOptions({ input: { id } }),
    staleTime: Infinity,
    meta: { quiet: true },
  }));

  const data = $derived(winners.data);
  const since = $derived(data?.before?.date.slice(0, 4) ?? null);
  const noun = $derived(data?.noun ?? "");
  const nouns = $derived(plural(noun));
  const tiers = $derived(
    (data?.tiers ?? []).map((tier) => ({
      ...tier,
      at: ORDER.indexOf(tier.code),
    })),
  );
  const tops = $derived(
    tiers.filter((tier) => tier.code === "metro" || tier.code === "seat"),
  );
  const topTotal = $derived(sum(tops.map((tier) => tier.total)));
  const topBefore = $derived(sum(tops.map((tier) => tier.before)));
  const parties = $derived(
    (data?.parties ?? []).map((party) => ({
      ...party,
      name:
        party.party == null
          ? m.sweep_independents()
          : (party.short ?? party.label),
      tone: swatch(party.color, party.key),
      top: sum(tops.map((tier) => party.won[tier.at] ?? 0)),
      topBefore: sum(tops.map((tier) => party.held[tier.at] ?? 0)),
      all: sum(tiers.map((tier) => party.won[tier.at] ?? 0)),
      allBefore: sum(tiers.map((tier) => party.held[tier.at] ?? 0)),
    })),
  );
  const byKey = $derived(new Map(parties.map((party) => [party.key, party])));
  const leaders = $derived(
    parties
      .filter((party) => party.top > 0)
      .toSorted((a, b) => b.top - a.top || (b.share ?? 0) - (a.share ?? 0)),
  );
  const rank = $derived(
    new Map(leaders.map((party, index) => [party.key, index])),
  );
  const voter = $derived(data?.measure == null ? null : parties[0]);
  const holder = $derived(leaders[0]);
  const lead = $derived(voter ?? holder);
  const verdict = $derived(
    lead == null
      ? ""
      : voter == null
        ? m.sweep_verdict_seats({ party: lead.name, nouns })
        : holder == null || holder === voter || holder.top === voter.top
          ? m.sweep_verdict_both({ party: voter.name, nouns })
          : m.sweep_verdict_split({
              party: voter.name,
              rival: holder.name,
              nouns,
            }),
  );
  const summary = $derived.by(() => {
    if (lead == null) {
      return "";
    }
    const won = count(lead.top);
    const total = count(topTotal);
    if (lead.share == null) {
      return m.sweep_summary_seats({ party: lead.name, won, total, nouns });
    }
    const share = percent(lead.share);
    return since == null || lead.earlier == null
      ? m.sweep_summary({ party: lead.name, won, total, nouns, share })
      : m.sweep_summary_since({
          party: lead.name,
          won,
          total,
          nouns,
          share,
          before: count(lead.topBefore),
          earlier: percent(lead.earlier),
          year: since,
        });
  });

  const regions = $derived(
    new Map((data?.groups ?? []).map((group, at) => [at, group.name])),
  );
  const seated = (row: { party?: string; label: string | null }) =>
    row.label == null
      ? "none"
      : row.party == null
        ? "independent"
        : `party:${row.party}`;
  const provinces = $derived(
    withEntries(data)
      .filter((row) => row.tier === "metro" || row.tier === "seat")
      .map((row) => {
        const key = seated(row);
        const prior =
          row.prior == null
            ? null
            : row.prior.startsWith("party:")
              ? row.prior
              : "independent";
        return {
          ...row,
          key,
          before: prior,
          name: regions.get(row.group ?? -1) ?? row.path,
          tone: byKey.get(key)?.tone ?? swatch(row.color, key),
          isMetro: row.tier === "metro",
        };
      })
      .toSorted(
        (a, b) =>
          (rank.get(a.key) ?? Infinity) - (rank.get(b.key) ?? Infinity) ||
          Number(b.isMetro) - Number(a.isMetro) ||
          a.name.localeCompare(b.name, getLocale()),
      ),
  );
  const changed = $derived(
    Map.groupBy(
      provinces.filter(
        (row) =>
          row.before != null && row.key !== "none" && row.before !== row.key,
      ),
      (row) => `${row.before}>${row.key}`,
    )
      .values()
      .map((members) => ({
        from: byKey.get(members[0]?.before ?? ""),
        to: byKey.get(members[0]?.key ?? ""),
        members: members.toSorted((a, b) =>
          a.name.localeCompare(b.name, getLocale()),
        ),
      }))
      .filter((pair) => pair.from != null && pair.to != null)
      .toArray()
      .toSorted((a, b) => b.members.length - a.members.length),
  );
  const bars = $derived(
    tiers
      .filter((tier) => tier.code === "district" || tier.code === "town")
      .map((tier) => ({
        ...tier,
        segments: parties
          .map((party) => ({
            key: party.key,
            name: party.name,
            tone: party.tone,
            won: party.won[tier.at] ?? 0,
          }))
          .filter((segment) => segment.won > 0),
      })),
  );
  const listed = $derived(
    isAll
      ? parties
      : parties.filter((party, index) => index < 8 || party.top > 0),
  );
  const widest = $derived(
    Math.max(...parties.map((party) => party.share ?? 0), 0.01),
  );

  function sum(values: Array<number>) {
    let total = 0;
    for (const value of values) {
      total += value;
    }
    return total;
  }

  function delta(value: number) {
    return new Intl.NumberFormat(getLocale(), { signDisplay: "exceptZero" })
      .format(value)
      .replace("-", "−");
  }
</script>

{#snippet change(now: number, then: number)}
  {#if since != null}
    <span
      class={cn("font-figures text-xs tabular-nums", {
        "text-muted-foreground": now === then,
      })}
      title={m.sweep_held({ count: count(then), year: since })}
      >{delta(now - then)}</span
    >
  {/if}
{/snippet}

{#if data != null && tiers.length > 0 && lead != null}
  <section
    class="col-span-12 grid gap-6 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <header class="grid gap-2">
      <h2
        id="{uid}-title"
        class="text-[clamp(1.5rem,2.6vw,2.125rem)]/tight font-[760] tracking-[-0.015em] text-balance max-inline-[32ch]"
      >
        {verdict}
      </h2>
      <p
        class="font-serif text-[clamp(1.0625rem,1.5vw,1.25rem)]/snug text-pretty max-inline-[60ch]"
      >
        {summary}
      </p>
    </header>

    <figure class="grid gap-2.5">
      <figcaption
        class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[0.8125rem]"
      >
        <span class="font-bold"
          >{capitalize(nouns)}
          <span
            class="font-figures font-normal text-muted-foreground tabular-nums"
            >{count(topTotal)}</span
          ></span
        >
        {#if provinces.some((row) => row.isMetro)}
          <span class="inline-flex items-center gap-1.5 text-muted-foreground">
            <span
              class="grid place-items-center rounded-xs bg-foreground block-3 inline-3"
              aria-hidden="true"
              ><span class="rounded-full bg-card block-1 inline-1"></span></span
            >{m.sweep_metro_key()}
          </span>
        {/if}
      </figcaption>
      <ol
        class="grid grid-cols-[repeat(var(--narrow),minmax(0,1fr))] gap-px sm:grid-cols-[repeat(var(--wide),minmax(0,1fr))]"
        style:--wide={provinces.length <= 90
          ? provinces.length
          : Math.ceil(provinces.length / 2)}
        style:--narrow={Math.ceil(provinces.length / 3)}
      >
        {#each provinces as row (row.path)}
          <li class="min-inline-0">
            <a
              class={cn(
                "grid aspect-square place-items-center rounded-[1px] bg-(--party) outline-offset-1 transition-opacity",
                { "opacity-35": focus != null && focus !== row.at },
              )}
              style:--party={row.tone}
              href="/{row.path}"
              title="{row.name}: {byKey.get(row.key)?.name ?? m.winners_none()}"
              aria-label="{row.name}: {byKey.get(row.key)?.name ??
                m.winners_none()}"
              onpointerenter={() => (focus = row.at)}
              onpointerleave={() => (focus = null)}
            >
              {#if row.isMetro}
                <span
                  class="rounded-full bg-card/90 block-[28%] inline-[28%]"
                  aria-hidden="true"
                ></span>
              {/if}
            </a>
          </li>
        {/each}
      </ol>
      <p class="flex flex-wrap gap-x-4 gap-y-1 text-[0.8125rem]">
        {#each leaders as party (party.key)}
          <span class="inline-flex items-baseline gap-1.5">
            <span
              class="self-center rounded-xs bg-(--party) block-2.5 inline-2.5"
              style:--party={party.tone}
            ></span>
            <span class="font-semibold">{party.name}</span>
            <span class="font-figures font-semibold tabular-nums"
              >{count(party.top)}</span
            >
            {@render change(party.top, party.topBefore)}
          </span>
        {/each}
      </p>
      {#if bars.length > 0}
        <div
          class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 pbs-1.5"
        >
          {#each bars as bar (bar.code)}
            <span class="text-[0.8125rem] font-bold"
              >{TITLES[bar.code]()}
              <span
                class="font-figures font-normal text-muted-foreground tabular-nums"
                >{count(bar.total)}</span
              ></span
            >
            <div
              class="flex gap-px overflow-hidden rounded-xs bg-secondary block-3"
              role="img"
              aria-label={bar.segments
                .map((segment) => `${segment.name} ${count(segment.won)}`)
                .join(", ")}
            >
              {#each bar.segments as segment (segment.key)}
                <span
                  class="bg-(--party) block-full"
                  style:--party={segment.tone}
                  style:flex-grow={segment.won}
                  title="{segment.name} {count(segment.won)}"
                ></span>
              {/each}
            </div>
          {/each}
        </div>
      {/if}
    </figure>

    <div class="grid gap-1.5">
      <h3 class="text-[0.8125rem] font-bold">{m.sweep_table_title()}</h3>
      <div class="-mx-3.5 overflow-x-auto">
        <table class="border-collapse text-sm inline-full">
          <thead>
            <tr class="text-[0.8125rem] text-muted-foreground">
              <th
                class="sticky inset-s-0 bg-card ps-3.5 pe-4 pbe-1.5 text-start font-normal"
                scope="col">{m.col_party()}</th
              >
              {#if data.measure != null}
                <th class="px-3 pbe-1.5 text-start font-normal" scope="col"
                  >{m.sweep_vote()}</th
                >
              {/if}
              {#each tiers as tier (tier.code)}
                <th
                  class="px-3 pbe-1.5 text-end align-bottom font-normal whitespace-nowrap"
                  scope="col"
                >
                  <span class="block font-semibold text-foreground"
                    >{TITLES[tier.code]()}</span
                  >
                  <span class="font-figures tabular-nums"
                    >{m.sweep_of({ count: count(tier.total) })}</span
                  >
                </th>
              {/each}
              {#if tiers.length > 1}
                <th
                  class="ps-3 pe-3.5 pbe-1.5 text-end align-bottom font-normal whitespace-nowrap"
                  scope="col"
                >
                  <span class="block font-semibold text-foreground"
                    >{m.sweep_all()}</span
                  >
                  <span class="font-figures tabular-nums"
                    >{m.sweep_of({
                      count: count(sum(tiers.map((tier) => tier.total))),
                    })}</span
                  >
                </th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each listed as party (party.key)}
              <tr class="border-bs border-border/60" style:--party={party.tone}>
                <th
                  class="sticky inset-s-0 bg-card py-2 ps-3.5 pe-4 text-start font-semibold whitespace-nowrap"
                  scope="row"
                >
                  <span class="inline-flex items-center gap-1.5">
                    <span
                      class="shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5"
                    ></span>
                    <Name
                      label={party.name}
                      party={party.party}
                      color={party.tone}
                      hasSwatch={false}
                    />
                  </span>
                </th>
                {#if data.measure != null}
                  <td class="px-3 py-2 min-inline-48">
                    <span class="flex items-center gap-2">
                      <span
                        class="font-figures font-semibold tabular-nums min-inline-12"
                        >{percent(party.share ?? 0)}</span
                      >
                      {#if since != null && party.earlier != null}
                        <span
                          class="font-figures text-xs text-muted-foreground tabular-nums min-inline-10"
                          title={m.sweep_since({ year: since })}
                          >{points((party.share ?? 0) - party.earlier, 1)}</span
                        >
                      {/if}
                      <span
                        class="grow bg-secondary block-1.5 max-sm:hidden"
                        aria-hidden="true"
                      >
                        <span
                          class="block bg-(--party) block-full"
                          style:inline-size="{((party.share ?? 0) / widest) *
                            100}%"
                        ></span>
                      </span>
                    </span>
                  </td>
                {/if}
                {#each tiers as tier (tier.code)}
                  {const won = $derived(party.won[tier.at] ?? 0)}
                  {const held = $derived(party.held[tier.at] ?? 0)}
                  <td class="px-3 py-2 text-end whitespace-nowrap">
                    <span
                      class={cn("font-figures tabular-nums", {
                        "font-semibold": won > 0,
                        "text-muted-foreground": won === 0,
                      })}>{count(won)}</span
                    >
                    {@render change(won, held)}
                  </td>
                {/each}
                {#if tiers.length > 1}
                  <td class="py-2 ps-3 pe-3.5 text-end whitespace-nowrap">
                    <span class="font-figures font-semibold tabular-nums"
                      >{count(party.all)}</span
                    >
                    {@render change(party.all, party.allBefore)}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if listed.length < parties.length || isAll}
        <button
          type="button"
          class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
          aria-expanded={isAll}
          onclick={() => (isAll = !isAll)}
          >{isAll
            ? m.winners_fewer()
            : m.winners_more({ count: count(parties.length) })}</button
        >
      {/if}
    </div>

    <Crowns maps={withLegend(data)} {hexes} {since} {year} bind:focus />

    {#if since != null}
      <div class="grid gap-2">
        <h3 class="text-[0.8125rem] font-bold">
          {m.sweep_changed_title({ year: since })}
        </h3>
        {#if changed.length === 0}
          <p class="text-sm text-muted-foreground">
            {m.sweep_changed_none({ noun, year: since })}
          </p>
        {:else}
          <ul class="grid gap-x-8 sm:grid-cols-2 xl:grid-cols-3">
            {#each changed as pair (`${pair.from?.key}>${pair.to?.key}`)}
              <li
                class="grid content-start gap-1 border-bs border-border/60 py-2"
              >
                <p
                  class="flex items-center gap-1.5 text-[0.8125rem] font-semibold"
                >
                  <span
                    class="rounded-xs bg-(--party) block-2.5 inline-2.5"
                    style:--party={pair.from?.tone}
                  ></span>
                  {pair.from?.name}
                  <span class="text-muted-foreground" aria-hidden="true">→</span
                  >
                  <span
                    class="rounded-xs bg-(--party) block-2.5 inline-2.5"
                    style:--party={pair.to?.tone}
                  ></span>
                  {pair.to?.name}
                  <span
                    class="ms-auto font-figures text-muted-foreground tabular-nums"
                    >{count(pair.members.length)}</span
                  >
                </p>
                <ul class="flex flex-wrap gap-x-1.5 text-sm/snug">
                  {#each pair.members as row (row.path)}
                    <li class="inline-flex not-last:after:content-[',']">
                      <Preview
                        class={cn("hover:highlighter", {
                          "font-semibold": row.isMetro,
                        })}
                        path={row.path}>{row.name}</Preview
                      >
                    </li>
                  {/each}
                </ul>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}

    {#if data.measure != null}
      <p class="text-xs text-pretty text-muted-foreground max-inline-[80ch]">
        {data.measure.mayors === 0
          ? m.sweep_measure_councils({
              count: count(data.measure.councils),
              nouns,
            })
          : data.measure.councils === 0
            ? m.sweep_measure_mayors({
                count: count(data.measure.mayors),
                nouns,
              })
            : m.sweep_measure_mixed({
                mayors: count(data.measure.mayors),
                councils: count(data.measure.councils),
                nouns,
              })}
      </p>
    {/if}
  </section>
{/if}
