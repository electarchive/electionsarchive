<script lang="ts">
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import type { Country } from "#lib/server/downloads.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import {
    countryName,
    electionLabel,
    formulaLabel,
    levelLabel,
    questionLabel,
    span,
  } from "#lib/downloads.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const { countries, end }: { countries: Array<Country>; end: number } =
    $props();

  let order = $state<"results" | "earliest" | "name">("results");

  const named = $derived(
    countries.map((country) => ({
      ...country,
      label: countryName(country.code, country.name),
    })),
  );
  const sorted = $derived(
    named.toSorted((a, b) => {
      if (order === "name") {
        return a.label.localeCompare(b.label);
      }
      if (order === "earliest") {
        return (a.first ?? end) - (b.first ?? end) || b.results - a.results;
      }
      return b.results - a.results || b.contests - a.contests;
    }),
  );
  const start = $derived(
    Math.floor(
      Math.min(end - 10, ...countries.flatMap((row) => row.first ?? [])) / 10,
    ) * 10,
  );
  const ticks = $derived(
    Array.from(
      { length: Math.floor((end - start) / 50) + 1 },
      (_, index) => Math.ceil(start / 50) * 50 + index * 50,
    ).filter((tick) => tick > start && tick < end),
  );
  const at = (value: number) => ((value - start) / (end + 1 - start)) * 100;
</script>

{#snippet facts(
  title: string,
  rows: Array<{ key: string; label: string; value: number; years: string }>,
)}
  {#if rows.length > 0}
    <div class="grid content-start gap-1.5">
      <h4 class="text-[0.8125rem] font-semibold">{title}</h4>
      <dl class="grid border-bs text-[0.8125rem]">
        {#each rows as row (row.key)}
          <div
            class="grid grid-cols-[minmax(0,1fr)_auto_4.75rem] gap-x-3 border-be py-1"
          >
            <dt class="truncate">{row.label}</dt>
            <dd class="text-end font-figures tabular-nums">
              {count(row.value)}
            </dd>
            <dd
              class="text-end font-figures text-muted-foreground tabular-nums"
            >
              {row.years}
            </dd>
          </div>
        {/each}
      </dl>
    </div>
  {/if}
{/snippet}

<div class="grid gap-3">
  <div class="flex flex-wrap items-end justify-between gap-3">
    <p class="text-sm text-pretty text-muted-foreground max-inline-[60ch]">
      {m.downloads_countries_lede()}
    </p>
    <Segmented
      options={[
        { value: "results", label: m.downloads_sort_results() },
        { value: "earliest", label: m.downloads_sort_earliest() },
        { value: "name", label: m.downloads_sort_name() },
      ]}
      bind:value={order}
      label={m.downloads_sort_label()}
      size="sm"
    />
  </div>

  <div class="grid text-sm">
    <div
      aria-hidden="true"
      class="grid grid-cols-[1rem_minmax(7rem,11rem)_5.25rem_minmax(0,1fr)_repeat(3,minmax(4.5rem,auto))_minmax(6rem,9rem)] items-end gap-x-4 border-be-2 border-foreground pbe-1.5 text-[0.75rem] font-semibold text-muted-foreground max-lg:hidden"
    >
      <span></span>
      <span>{m.downloads_col_country()}</span>
      <span>{m.downloads_col_years()}</span>
      <span class="relative block-4">
        {#each ticks as tick (tick)}
          <span
            class="absolute -translate-x-1/2 font-figures font-normal tabular-nums"
            style:inset-inline-start="{at(tick)}%">{tick}</span
          >
        {/each}
      </span>
      <span class="text-end">{m.downloads_col_elections()}</span>
      <span class="text-end">{m.downloads_col_contests()}</span>
      <span class="text-end">{m.downloads_col_results()}</span>
      <span>{m.downloads_col_finest()}</span>
    </div>

    {#each sorted as country (country.code)}
      {const finest = $derived(country.units.at(-1))}
      <details class="group border-be">
        <summary
          class="grid cursor-pointer list-none grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 py-2 hover:bg-secondary/60 focus-visible:outline-2 focus-visible:outline-ring lg:grid-cols-[1rem_minmax(7rem,11rem)_5.25rem_minmax(0,1fr)_repeat(3,minmax(4.5rem,auto))_minmax(6rem,9rem)] [&::-webkit-details-marker]:hidden"
        >
          <ChevronRightIcon
            class="text-muted-foreground transition-transform block-3.5 inline-3.5 group-open:rotate-90 motion-reduce:transition-none"
            aria-hidden="true"
          />
          <span class="truncate font-semibold">{country.label}</span>
          <span
            class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums max-lg:col-start-2 max-lg:row-start-2"
            ><span class="sr-only">{m.downloads_col_years()}: </span>{span(
              country.first,
              country.last,
            )}</span
          >
          <span
            aria-hidden="true"
            class="relative block-5 max-lg:col-span-full max-lg:col-start-2 max-lg:row-start-3"
          >
            {#each ticks as tick (tick)}
              <span
                aria-hidden="true"
                class="inset-block-0 absolute border-s border-border"
                style:inset-inline-start="{at(tick)}%"
              ></span>
            {/each}
            {#if country.first != null}
              <span
                aria-hidden="true"
                class="absolute inset-bs-1/2 -translate-y-1/2 rounded-[1px] bg-foreground block-1.5"
                style:inset-inline-start="{at(country.first)}%"
                style:inline-size="max(3px, {at((country.last ?? end) + 1) -
                  at(country.first)}%)"
              ></span>
            {/if}
          </span>
          <span class="text-end font-figures tabular-nums max-lg:hidden"
            ><span class="sr-only">{m.downloads_col_elections()}: </span>{count(
              country.elections,
            )}</span
          >
          <span class="text-end font-figures tabular-nums max-lg:hidden"
            ><span class="sr-only">{m.downloads_col_contests()}: </span>{count(
              country.contests,
            )}</span
          >
          <span
            class="text-end font-figures font-semibold tabular-nums max-lg:col-start-3 max-lg:row-start-1"
            ><span class="sr-only">{m.downloads_col_results()}: </span>{count(
              country.results,
            )}</span
          >
          <span
            class="truncate text-muted-foreground max-lg:col-start-3 max-lg:row-start-2 max-lg:text-end max-lg:text-[0.8125rem]"
            >{#if finest?.noun != null}<span class="sr-only"
                >{m.downloads_col_finest()}:
              </span>{finest.noun}{/if}</span
          >
        </summary>
        <div
          class="grid gap-x-8 gap-y-5 ps-8 pbs-2 pbe-5 sm:grid-cols-2 xl:grid-cols-3"
        >
          {@render facts(
            m.downloads_levels(),
            country.levels.map((level) => ({
              key: level.kind,
              label: levelLabel(level.kind),
              value: level.contests,
              years: span(level.first, level.last),
            })),
          )}
          {@render facts(
            m.downloads_units(),
            country.units
              .toReversed()
              .map((unit) => ({
                key: unit.scheme,
                label: unit.noun ?? unit.scheme,
                value: unit.results,
                years: span(unit.first, unit.last),
              })),
          )}
          {@render facts(
            m.downloads_election_kinds(),
            country.election_kinds.map((kind) => ({
              key: kind.kind,
              label: electionLabel(kind.kind),
              value: kind.count,
              years: span(kind.first, kind.last),
            })),
          )}
          {@render facts(
            m.downloads_questions(),
            country.ballot_questions.map((question) => ({
              key: question.type,
              label: questionLabel(question.type),
              value: question.count,
              years: span(question.first, null),
            })),
          )}
          {@render facts(
            m.downloads_formulas(),
            country.formulas.map((formula) => ({
              key: formula.formula,
              label: formulaLabel(formula.formula),
              value: formula.contests,
              years: "",
            })),
          )}
          <p class="self-end text-[0.8125rem] sm:col-span-full">
            <a
              href="/{country.code.toLowerCase()}"
              class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              >{m.downloads_country_open({ country: country.label })}</a
            >
          </p>
        </div>
      </details>
    {/each}
  </div>
</div>
