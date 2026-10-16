<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";
  import Career from "./career.svelte";

  const { data }: { data: PageData } = $props();

  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
  };

  const person = $derived(data.person);
  const entries = $derived(
    person.record.map((entry) => ({
      ...entry,
      share:
        entry.votes == null || entry.total == null || entry.total === 0
          ? null
          : entry.votes / entry.total,
    })),
  );
  const won = $derived(
    entries.filter(
      (entry) => entry.outcome === "elected" || entry.outcome === "won",
    ).length,
  );
  const years = $derived(entries.map((entry) => entry.year));
  const today = new Date().toISOString().slice(0, 10);
  const low = $derived(
    Math.min(
      ...person.served.flatMap((spell) =>
        spell.from == null ? [] : [Date.parse(spell.from)],
      ),
      ...years.map((year) => Date.UTC(year, 0, 1)),
      Date.parse(today),
    ),
  );
  const at = (value: string | null) =>
    ((value == null ? Date.parse(today) : Date.parse(value)) - low) /
    Math.max(Date.parse(today) - low, 1);
</script>

<svelte:head>
  <title>{person.name} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta property="og:type" content="profile" />
  <meta property="og:title" content={person.name} />
  <meta
    property="og:image"
    content="{page.url.origin}/og{page.url.pathname}.png"
  />
  <meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<main class="pbe-16">
  <article
    style:--party={person.party == null
      ? "var(--foreground)"
      : swatch(person.party.color, `party:${person.party.id}`)}
    class="grid grid-cols-12 gap-px overflow-clip border-be bg-border"
  >
    <header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
      <div class="flex items-start justify-between gap-3">
        <h1 class="title">
          {person.name}
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
        {#if person.party != null}
          <Name
            class="text-foreground"
            label={person.party.label}
            color={swatch(person.party.color, `party:${person.party.id}`)}
            party={person.party.id}
          />
        {/if}
        {#if person.born != null}
          <span
            >{person.precision === "day"
              ? m.card_born_date({ date: date(person.born) })
              : m.card_born({ year: person.born.slice(0, 4) })}</span
          >
        {/if}
        {#if person.wikidata != null}
          <a
            class="inline-flex items-center gap-1 underline decoration-border underline-offset-4 hover:decoration-foreground"
            href="https://www.wikidata.org/wiki/{person.wikidata}"
            rel="external noopener"
            >{m.card_wikidata()}<ArrowUpRightIcon class="block-3 inline-3" /></a
          >
        {/if}
      </p>
    </header>

    {#if entries.length > 0 || person.served.length > 0}
      <section class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5">
        {#if entries.length > 0}
          <p
            class="font-serif text-[clamp(1.0625rem,1.6vw,1.25rem)]/snug text-pretty max-inline-[60ch]"
          >
            {entries.length === 1
              ? m.person_lede_single({ year: String(years[0]) })
              : m.person_lede({
                  won: count(won),
                  count: count(entries.length),
                  first: String(Math.min(...years)),
                  last: String(Math.max(...years)),
                })}
          </p>
        {/if}
        {#if person.served.length > 0}
          <div class="grid gap-1.5">
            <h2 class="text-[0.8125rem] font-bold">{m.person_served()}</h2>
            <ul class="grid text-sm">
              {#each person.served as spell, index (index)}
                {const x0 = $derived(at(spell.from))}
                {const x1 = $derived(at(spell.to))}
                <li
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-be border-border/60 py-1.5 last:border-be-0 sm:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto]"
                >
                  {#if spell.isBody}
                    <a
                      class="truncate font-semibold hover:highlighter"
                      href={resolve("/id/[id]", { id: spell.id })}
                      >{spell.name}</a
                    >
                  {:else}
                    <span class="truncate font-semibold">{spell.name}</span>
                  {/if}
                  <span
                    class="relative block rounded-full bg-muted block-2 max-sm:col-span-2 max-sm:row-start-2"
                    aria-hidden="true"
                  >
                    <span
                      class="absolute inset-y-0 rounded-full bg-(--party)"
                      style:inset-inline-start="{x0 * 100}%"
                      style:inline-size="{Math.max(x1 - x0, 0.01) * 100}%"
                    ></span>
                  </span>
                  <span
                    class="font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
                    >{spell.to == null
                      ? m.person_served_now({
                          from: spell.from?.slice(0, 4) ?? "",
                        })
                      : m.person_served_span({
                          from: spell.from?.slice(0, 4) ?? "",
                          to: spell.to.slice(0, 4),
                        })}</span
                  >
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </section>
    {/if}

    <Career record={person.record} />

    <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5">
      <h2 class="text-[0.8125rem] font-bold">{m.person_record()}</h2>
      {#if entries.length === 0}
        <p class="text-sm text-muted-foreground">{m.person_empty()}</p>
      {:else}
        <ol class="grid text-sm">
          {#each entries as entry, index (index)}
            <li
              class="grid grid-cols-[3rem_minmax(0,1fr)_7.5rem] items-baseline gap-x-3 border-be border-border/60 py-2 last:border-be-0 max-sm:grid-cols-[2.75rem_minmax(0,1fr)_4rem]"
            >
              <span
                class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                >{entry.year}</span
              >
              <span class="grid gap-0.5 min-inline-0">
                <span>
                  {#if entry.path == null}
                    <span class="font-semibold">{entry.name}</span>
                  {:else}
                    <a
                      class="font-semibold hover:highlighter"
                      href={resolve("/[...path]", { path: entry.path })}
                      >{entry.name}</a
                    >
                  {/if}
                  {#if entry.outcome != null}
                    <span
                      class="ms-1 highlighter px-1 text-xs font-semibold whitespace-nowrap"
                      >{OUTCOMES[entry.outcome]?.() ?? entry.outcome}</span
                    >
                  {/if}
                </span>
                {#if entry.party != null}
                  <Name
                    class="justify-self-start text-[0.8125rem] text-muted-foreground"
                    label={entry.party.label}
                    color={swatch(entry.party.color, `party:${entry.party.id}`)}
                    party={entry.party.id}
                  />
                {/if}
              </span>
              <span class="grid justify-items-end gap-1">
                <span class="font-figures text-[0.8125rem] tabular-nums"
                  >{entry.share == null ? "–" : percent(entry.share)}</span
                >
                {#if entry.share != null}
                  <span
                    class="flex overflow-hidden rounded-xs bg-muted block-1.5 inline-full"
                    aria-hidden="true"
                  >
                    <span
                      class="block-full"
                      style:inline-size="{entry.share * 100}%"
                      style:background-color={swatch(
                        entry.party?.color ?? null,
                        entry.party == null ? "" : `party:${entry.party.id}`,
                      )}
                    ></span>
                  </span>
                {/if}
                {#if entry.electoral != null}
                  <span
                    class="font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                    >{m.series_electoral({
                      votes: count(entry.electoral.votes),
                      total: count(entry.electoral.total),
                    })}</span
                  >
                {/if}
              </span>
            </li>
          {/each}
        </ol>
      {/if}
    </section>
  </article>
</main>
