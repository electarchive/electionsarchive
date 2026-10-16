<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import { count, date } from "#lib/format.js";
  import { palette } from "#lib/palette.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
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
  };
  const RELATIONS: Record<
    string,
    [(input: { name: string }) => string, (input: { name: string }) => string]
  > = {
    merged_into: [m.rel_merged_into_out, m.rel_merged_into_in],
    split_from: [m.rel_split_from_out, m.rel_split_from_in],
    successor_of: [m.rel_successor_of_out, m.rel_successor_of_in],
    refounded_as: [m.rel_refounded_as_out, m.rel_refounded_as_in],
    absorbed_by: [m.rel_absorbed_by_out, m.rel_absorbed_by_in],
    list_vehicle_for: [m.rel_list_vehicle_for_out, m.rel_list_vehicle_for_in],
  };
  const AFFILIATIONS: Record<string, (input: { name: string }) => string> = {
    ep_group: m.aff_ep_group,
    federation: m.aff_federation,
    international: m.aff_international,
    regional_branch: m.aff_regional_branch,
  };

  const party = $derived(data.party);
  const color = $derived(swatch(party.color, `party:${data.id}`));
  const links = $derived([
    ...party.relations.flatMap((relation) => {
      const phrase = RELATIONS[relation.kind]?.[relation.outgoing ? 0 : 1];
      return phrase == null
        ? []
        : [
            {
              text: phrase({ name: relation.party.label }),
              party: relation.party,
              year: relation.date?.slice(0, 4) ?? null,
            },
          ];
    }),
    ...party.parents.flatMap((parent) => {
      const phrase = AFFILIATIONS[parent.kind];
      return phrase == null
        ? []
        : [
            {
              text: phrase({ name: parent.party.label }),
              party: parent.party,
              year: null,
            },
          ];
    }),
  ]);
  const years = $derived(
    Map.groupBy(data.record.elections, (election) => election.year)
      .entries()
      .toArray(),
  );
</script>

<svelte:head>
  <title>{party.name} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
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
    <header
      class="col-span-12 grid gap-1.5 border-bs-[6px] border-(--party) bg-card px-3.5 pbs-4 pbe-3.5"
    >
      <div class="flex items-start justify-between gap-3">
        <h1 class="title">
          {party.name}
          {#if party.short != null && party.short !== party.name}
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
        {#if party.wikidata != null}
          <a
            class="inline-flex items-center gap-1 underline decoration-border underline-offset-4 hover:decoration-foreground"
            href="https://www.wikidata.org/wiki/{party.wikidata}"
            rel="external noopener"
            >{m.card_wikidata()}<ArrowUpRightIcon class="block-3 inline-3" /></a
          >
        {/if}
        <span
          class="inline-flex items-center gap-2"
          title={m.party_color_note()}
        >
          <label class="inline-flex items-center gap-1.5">
            <input
              class="cursor-pointer rounded-xs border border-input bg-card p-0 block-5 inline-7"
              type="color"
              value={palette.overrides[data.id] ?? party.color ?? "#888888"}
              oninput={(event) =>
                (palette.overrides[data.id] = event.currentTarget.value)}
            />
            {m.party_color_pick({ name: party.short ?? party.name })}
          </label>
          {#if palette.overrides[data.id] != null}
            <button
              type="button"
              class="underline decoration-border underline-offset-4 hover:decoration-foreground"
              onclick={() =>
                (palette.overrides = Object.fromEntries(
                  Object.entries(palette.overrides).filter(
                    ([key]) => key !== data.id,
                  ),
                ))}>{m.party_color_reset()}</button
            >
          {/if}
        </span>
      </p>
    </header>

    {#if party.span != null}
      <dl class="col-span-12 grid grid-cols-3 gap-px">
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_ballot()}
          </dt>
          <dd
            class="mbs-0.5 font-figures text-2xl tracking-[-0.02em] tabular-nums"
          >
            {party.span.from.slice(0, 4) === party.span.to.slice(0, 4)
              ? party.span.from.slice(0, 4)
              : m.polity_span({
                  first: party.span.from.slice(0, 4),
                  last: party.span.to.slice(0, 4),
                })}
          </dd>
        </div>
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_elections()}
          </dt>
          <dd
            class="mbs-0.5 font-figures text-2xl tracking-[-0.02em] tabular-nums"
          >
            {count(data.record.count)}
          </dd>
        </div>
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.party_contests()}
          </dt>
          <dd
            class="mbs-0.5 font-figures text-2xl tracking-[-0.02em] tabular-nums"
          >
            {count(party.span.contests)}
          </dd>
        </div>
      </dl>
    {/if}

    <History history={data.history} name={party.name} {color} />

    {#if data.election != null}
      <Strength election={data.election} party={data.id} name={party.name} />
    {/if}

    <Lineage {party} {color} />

    {#if links.length > 0}
      <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5">
        <h2 class="text-[0.8125rem] font-bold">{m.party_relations()}</h2>
        <ul class="grid gap-1 text-sm">
          {#each links as link, index (index)}
            <li class="flex items-baseline gap-2">
              <span
                class="shrink-0 self-center rounded-xs block-2 inline-2"
                style:background-color={swatch(
                  link.party.color,
                  `party:${link.party.id}`,
                )}
              ></span>
              <a
                class="hover:highlighter"
                href={resolve("/id/[id]", { id: link.party.id })}>{link.text}</a
              >
              {#if link.year != null}
                <span
                  class="font-figures text-xs text-muted-foreground tabular-nums"
                  >{link.year}</span
                >
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5">
      <h2 class="text-[0.8125rem] font-bold">{m.party_record()}</h2>
      {#if years.length === 0}
        <p class="text-sm text-muted-foreground">{m.party_none()}</p>
      {:else}
        <ol class="grid">
          {#each years as [year, entries] (year)}
            <li
              class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1.5 last:border-be-0"
            >
              <span
                class="font-figures text-[0.9375rem] tracking-[-0.02em] tabular-nums"
                >{year ?? ""}</span
              >
              <ul class="grid gap-1 text-sm">
                {#each entries as election, index (index)}
                  <li
                    class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
                  >
                    {#if election.path == null}
                      <span>{election.name}</span>
                    {:else}
                      <a
                        class="hover:highlighter"
                        href={resolve("/[...path]", { path: election.path })}
                        title={date(election.date)}>{election.name}</a
                      >
                    {/if}
                    <span
                      class="text-xs whitespace-nowrap text-muted-foreground tabular-nums"
                      >{m.race_contests({
                        count: election.races,
                      })}{#if election.won > 0}, <span
                          class="font-semibold text-foreground"
                          >{m.party_won({ count: count(election.won) })}</span
                        >{/if}</span
                    >
                  </li>
                {/each}
              </ul>
            </li>
          {/each}
        </ol>
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
