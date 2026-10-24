<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import { SvelteSet } from "svelte/reactivity";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { AFFILIATIONS } from "#lib/relations.js";
  import type { PageData } from "./$types";
  import History from "./history.svelte";
  import Lineage from "./lineage.svelte";
  import Strength from "./strength.svelte";

  const { data }: { data: PageData } = $props();

  const KINDS: Record<string, () => string> = {
    party: m.party_kind_party,
    independent_group: m.party_kind_independent_group,
    list_vehicle: m.party_kind_list_vehicle,
    federation: m.party_kind_federation,
    bloc: m.party_kind_bloc,
    nuance: m.party_kind_nuance,
    parliamentary_group: m.party_kind_parliamentary_group,
    caucus: m.party_kind_caucus,
  };

  const party = $derived(data.party);
  const isAbroad = $derived(
    party.scope?.polity.path != null &&
      party.polity?.path != null &&
      !party.scope.polity.path.startsWith(`${party.polity.path}/`),
  );
  const color = $derived(swatch(party.color, `party:${data.id}`));
  const links = $derived(
    party.parents.flatMap((parent) => {
      const phrase = AFFILIATIONS[parent.kind];
      return phrase == null
        ? []
        : [{ text: phrase({ name: parent.party.label }), party: parent.party }];
    }),
  );
  const years = $derived(
    Map.groupBy(data.record.elections, (election) => election.year)
      .entries()
      .map(
        ([year, entries]) =>
          [year, entries.toSorted((a, b) => b.races - a.races)] as const,
      )
      .toArray(),
  );
  const places = $derived(
    new Intl.ListFormat(getLocale(), { type: "conjunction" })
      .formatToParts(data.abroad.map((_, index) => String(index)))
      .map((part) => ({
        text: part.value,
        place:
          part.type === "element" ? data.abroad[Number(part.value)] : undefined,
      })),
  );
  let isEvery = $state(false);
  const shown = $derived(
    isEvery || years.length <= 11 ? years : years.slice(0, 10),
  );
  const opened = new SvelteSet<number | null>();
  const months = new Intl.DateTimeFormat(getLocale(), {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
</script>

<svelte:head>
  <title
    >{party.scope == null
      ? party.name
      : (isAbroad ? m.party_abroad_title : m.party_scope_title)({
          party: party.name,
          place: party.scope.polity.name,
        })} | {m.wordmark()}</title
  >
  <link
    rel="canonical"
    href="{page.url.origin}{page.url.pathname}{party.scope == null
      ? ''
      : page.url.search}"
  />
  <meta property="og:type" content="profile" />
  <meta property="og:title" content={party.name} />
  <meta
    property="og:image"
    content="{page.url.origin}/og{page.url.pathname}.png"
  />
  <meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<main class="pbe-16">
  <article
    class="grid grid-cols-12 gap-px overflow-clip border-be bg-border"
    style:--party={color}
  >
    <header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
      <div class="flex items-start justify-between gap-3">
        <h1 class="title">
          {party.scope == null
            ? party.name
            : (isAbroad ? m.party_abroad_title : m.party_scope_title)({
                party: party.name,
                place: party.scope.polity.name,
              })}
          {#if party.scope == null && party.short != null && !party.name.includes(party.short)}
            <span
              class="ms-1 font-sans text-[0.5em] font-semibold tracking-normal text-muted-foreground"
              >{party.short}</span
            >
          {/if}
        </h1>
        <a
          class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
          href={resolve(`/compare?a=${data.id}`)}
          ><ArrowLeftRightIcon
            class="block-3.5 inline-3.5"
          />{m.versus_action()}</a
        >
      </div>
      {#if party.scope != null}
        {const scope = $derived(party.scope)}
        <p
          class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
        >
          <span
            >{m.party_scope_national()}
            <a
              class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/parties/[...path]", {
                path: page.params.path ?? "",
              })}>{party.name}</a
            ></span
          >
          {#if scope.branches.length > 0}
            <span
              >{m.party_scope_branch({ place: scope.polity.name })}
              {#each scope.branches as branch, index (branch.id)}{#if index > 0},
                {/if}<a
                  class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={resolve("/id/[id]", { id: branch.id })}
                  >{branch.label}</a
                >{/each}</span
            >
          {/if}
          {#if scope.polity.path != null}
            <a
              class="underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/[...path]", { path: scope.polity.path })}
              >{scope.polity.name}</a
            >
          {/if}
        </p>
      {/if}
      {#if party.scope == null}
        <p
          class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
        >
          {#if party.polity != null}
            <span>
              {#if party.polity.path == null}
                {m.party_in({
                  kind: KINDS[party.kind]?.() ?? party.kind,
                  polity: party.polity.name,
                })}
              {:else}
                <a
                  class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: party.polity.path })}
                  >{m.party_in({
                    kind: KINDS[party.kind]?.() ?? party.kind,
                    polity: party.polity.name,
                  })}</a
                >
              {/if}
            </span>
          {/if}
          {#if data.abroad.length > 0}
            {const frame = $derived(
              m.party_abroad({ places: "\u0001" }).split("\u0001"),
            )}
            <span
              >{frame[0]}{#each places as part, index (index)}{#if part.place == null}{part.text}{:else}<a
                    class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                    href="{resolve('/parties/[...path]', {
                      path: page.params.path ?? '',
                    })}?in={part.place.path}">{part.place.name}</a
                  >{/if}{/each}{frame[1]}</span
            >
          {/if}
          {#if party.wikidata != null}
            <a
              class="inline-flex items-center gap-1 underline decoration-border underline-offset-4 hover:decoration-foreground"
              href="https://www.wikidata.org/wiki/{party.wikidata}"
              rel="external noopener"
              >{m.card_wikidata()}<ArrowUpRightIcon
                class="block-3 inline-3"
              /></a
            >
          {/if}
        </p>
      {/if}
    </header>

    {const span = $derived(party.scope == null ? party.span : party.scope.span)}
    {#if span != null}
      <dl
        class="col-span-12 flex flex-wrap items-baseline gap-x-7 gap-y-1.5 bg-card px-3.5 py-3"
      >
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_ballot()}
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {span.from.slice(0, 4) === span.to.slice(0, 4)
              ? span.from.slice(0, 4)
              : m.polity_span({
                  first: span.from.slice(0, 4),
                  last: span.to.slice(0, 4),
                })}
          </dd>
        </div>
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_elections()}
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {count(data.record.count)}
          </dd>
        </div>
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_contests()}
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {count(span.contests)}
          </dd>
        </div>
      </dl>
    {/if}

    <History
      history={data.history}
      name={party.name}
      short={party.short}
      id={data.id}
      fallback={party.color}
      {color}
      notes={data.notes}
    />

    {#if data.elections.length > 0}
      <Strength elections={data.elections} party={data.id} name={party.name} />
    {/if}

    {#if data.members.length > 0}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3.5 pbe-3"
        aria-labelledby="members-title"
      >
        <div class="grid gap-0.5">
          <h2 id="members-title" class="heading">
            {m.party_members_title()}
          </h2>
          <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
            {m.party_members_lede({ name: party.name })}
          </p>
        </div>
        <ul class="grid text-sm">
          {#each data.members as member, index (index)}
            {const from = $derived(
              member.from == null ? null : new Date(`${member.from}T00:00:00Z`),
            )}
            {const to = $derived(
              member.to == null
                ? null
                : new Date(Date.parse(`${member.to}T00:00:00Z`) - 864e5),
            )}
            <li
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 py-1.5 last:border-be-0"
            >
              <a
                class="hover:highlighter"
                href={resolve("/id/[id]", { id: member.person })}
                >{member.name}</a
              >
              <span
                class="font-figures text-xs whitespace-nowrap text-muted-foreground tabular-nums"
                >{from != null && to != null
                  ? months.formatRange(from, to)
                  : from != null
                    ? m.party_member_since({ date: months.format(from) })
                    : to != null
                      ? m.party_member_until({ date: months.format(to) })
                      : ""}</span
              >
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if party.scope == null}
      <Lineage {party} {color} {links} />
    {/if}

    <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5">
      <h2 class="text-[0.8125rem] font-bold">{m.party_record()}</h2>
      {#if years.length === 0}
        <p class="text-sm text-muted-foreground">{m.party_none()}</p>
      {:else}
        <ol class="grid">
          {#each shown as [year, entries] (year)}
            <li
              class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1.5 last:border-be-0"
            >
              <span
                class="font-figures text-[0.9375rem] tracking-[-0.02em] tabular-nums"
                >{year ?? ""}</span
              >
              {const prefix = $derived(
                new RegExp(`^(?:\\p{L}+ (?:\\d{1,2}, )?)?${year} `, "u"),
              )}
              <ul class="grid gap-1 text-sm">
                {#each opened.has(year) ? entries : entries.slice(0, 5) as election, index (index)}
                  {const name = $derived.by(() => {
                    const bare = election.name.replace(prefix, "");
                    return bare.charAt(0).toUpperCase() + bare.slice(1);
                  })}
                  {const day = $derived(
                    election.date != null &&
                      entries.some(
                        (other) =>
                          other !== election &&
                          other.name.replace(prefix, "").toLowerCase() ===
                            name.toLowerCase(),
                      )
                      ? new Intl.DateTimeFormat(getLocale(), {
                          month: "long",
                          day: "numeric",
                          timeZone: "UTC",
                        }).format(new Date(`${election.date}T00:00:00Z`))
                      : null,
                  )}
                  <li
                    class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
                  >
                    <span>
                      {#if election.path == null}
                        {name}
                      {:else}
                        <a
                          class="hover:highlighter"
                          href={resolve("/[...path]", { path: election.path })}
                          title={date(election.date)}>{name}</a
                        >
                      {/if}
                      {#if day != null}
                        <span class="text-muted-foreground">{day}</span>
                      {/if}
                    </span>
                    <span
                      class="text-xs whitespace-nowrap text-muted-foreground tabular-nums"
                      >{m.race_contests({
                        count: count(election.races),
                      })}{#if election.seats > 0 || election.won > 0}, <span
                          class="font-semibold text-foreground"
                          >{election.seats > 0 && election.won > 0
                            ? m.party_seats_offices_won({
                                seats: election.seats,
                                offices: election.won,
                              })
                            : election.seats > 0
                              ? m.party_seats_won({ count: election.seats })
                              : m.party_won({
                                  count: count(election.won),
                                })}</span
                        >{/if}</span
                    >
                  </li>
                {/each}
                {#if entries.length > 5 && !opened.has(year)}
                  <li>
                    <button
                      type="button"
                      class="text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                      onclick={() => opened.add(year)}
                      >{m.party_record_year_more({
                        count: count(entries.length),
                      })}</button
                    >
                  </li>
                {/if}
              </ul>
            </li>
          {/each}
        </ol>
        {#if shown.length < years.length}
          <button
            type="button"
            class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
            onclick={() => (isEvery = true)}
            >{m.party_record_years_more({
              count: count(years.length - shown.length),
            })}</button
          >
        {/if}
        {#if data.record.count > data.record.elections.length}
          <p class="text-xs text-muted-foreground">
            {m.party_record_more({
              shown: count(data.record.elections.length),
              count: count(data.record.count),
            })}
          </p>
        {/if}
      {/if}
    </section>
  </article>
</main>
