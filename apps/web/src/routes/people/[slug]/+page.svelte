<script lang="ts">
  import { resolve } from "$app/paths";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

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
</script>

<svelte:head>
  <title>{person.name} | {m.wordmark()}</title>
</svelte:head>

<main class="p-3.5 max-sm:p-0">
  <article
    class="grid grid-cols-12 gap-px overflow-hidden rounded-lg border bg-border max-sm:rounded-none max-sm:border-x-0"
  >
    <header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
      <h1
        class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
      >
        {person.name}
      </h1>
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

    {#if entries.length > 0}
      <dl class="col-span-12 grid grid-cols-3 gap-px">
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.person_ran()}
          </dt>
          <dd
            class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums"
          >
            {count(entries.length)}
          </dd>
        </div>
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.person_won()}
          </dt>
          <dd
            class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums"
          >
            {count(won)}
          </dd>
        </div>
        <div class="bg-card px-3.5 py-3">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.person_years()}
          </dt>
          <dd
            class="mbs-0.5 font-mono text-2xl tracking-[-0.02em] tabular-nums"
          >
            {Math.min(...years) === Math.max(...years)
              ? Math.min(...years)
              : m.polity_span({
                  first: String(Math.min(...years)),
                  last: String(Math.max(...years)),
                })}
          </dd>
        </div>
      </dl>
    {/if}

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
                class="font-mono text-[0.8125rem] text-muted-foreground tabular-nums"
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
                <span class="font-mono text-[0.8125rem] tabular-nums"
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
                    class="font-mono text-[0.6875rem] text-muted-foreground tabular-nums"
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
