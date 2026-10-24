<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { count, credit, date, percent } from "#lib/format.js";
  import { explain, LOST } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Career from "./career.svelte";
  import Parties from "./parties.svelte";

  const { data }: { data: PageData } = $props();

  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
    lost: m.person_lost_mark,
    elsewhere: m.person_elected_elsewhere,
    withdrew: m.outcome_withdrew,
    disqualified: m.outcome_disqualified,
  };

  let isOpen = $state(false);
  const person = $derived(data.person);
  const entries = $derived(
    person.record.map((entry) => ({
      ...entry,
      name:
        entry.kind === "seated"
          ? entry.name === ""
            ? m.person_record_by_election_unplaced()
            : m.person_record_by_election({ place: entry.name })
          : entry.name.startsWith(`${entry.year} `)
            ? `${entry.name.charAt(String(entry.year).length + 1).toLocaleUpperCase()}${entry.name.slice(String(entry.year).length + 2)}`
            : entry.name,
      share:
        entry.votes == null || entry.total == null || entry.total === 0
          ? null
          : entry.votes / entry.total,
      isMuted:
        entry.outcome != null &&
        (LOST.has(entry.outcome) || entry.outcome === "elsewhere"),
    })),
  );
  const races = $derived(entries.map((entry) => entry.outcome));
  const won = $derived(
    races.filter((outcome) => outcome === "elected" || outcome === "won")
      .length,
  );
  const advanced = $derived(
    races.filter((outcome) => outcome === "advanced").length,
  );
  const years = $derived(entries.map((entry) => entry.year));
  const first = $derived(Math.min(...years));
  const last = $derived(Math.max(...years));
  const today = new Date().toISOString().slice(0, 10);
  const endings: Record<string, () => string> = {
    died: m.person_ended_died,
    resigned: m.person_ended_resigned,
    elevated: m.person_ended_elevated,
    appointed: m.person_ended_appointed,
    annulled: m.person_ended_annulled,
    disqualified: m.person_ended_disqualified,
    expelled: m.person_ended_expelled,
    removed: m.person_ended_removed,
    withdrew: m.person_ended_withdrew,
    lost_seat: m.person_ended_lost_seat,
  };
  const end = $derived(
    person.isGone
      ? (person.died ??
          [
            ...[...person.served, ...person.elected].flatMap((spell) => [
              spell.from,
              spell.to,
            ]),
            ...entries.map((entry) => entry.date),
          ]
            .filter((value) => value != null)
            .toSorted((a, b) => a.localeCompare(b))
            .at(-1) ??
          today)
      : today,
  );
  const low = $derived(
    Math.min(
      ...person.served.flatMap((spell) =>
        spell.from == null ? [] : [Date.parse(spell.from)],
      ),
      ...person.elected.map((spell) => Date.parse(spell.from)),
      ...years.map((year) => Date.UTC(year, 0, 1)),
      Date.parse(end),
    ),
  );
  const high = $derived(
    Math.max(
      Date.parse(end),
      ...person.elected.flatMap((spell) =>
        spell.to == null ? [] : [Date.parse(spell.to)],
      ),
    ),
  );
  const at = (value: string | null) =>
    (Date.parse(value ?? end) - low) / Math.max(high - low, 1);
  const since = $derived(
    person.isGone ? m.person_served_from : m.person_served_now,
  );
</script>

{#snippet spells(
  heading: string,
  note: string | null,
  list: Array<{
    id: string;
    name: string;
    isBody: boolean;
    path: string | null;
    from: string | null;
    to: string | null;
    seats?: Array<{
      name: string;
      from: string | null;
      to: string | null;
      ended: string | null;
    }>;
  }>,
)}
  <div class="grid gap-1.5">
    <h2 class="text-[0.8125rem] font-bold">{heading}</h2>
    {#if note != null}
      <p class="text-xs text-muted-foreground max-inline-[60ch]">{note}</p>
    {/if}
    <ul class="grid text-sm">
      {#each list as spell, index (index)}
        {const x0 = $derived(at(spell.from))}
        {const x1 = $derived(
          note != null && spell.to == null ? x0 : at(spell.to),
        )}
        <li
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-be border-border/60 py-1.5 last:border-be-0 sm:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto]"
        >
          {#if spell.isBody}
            <a
              class="truncate font-semibold hover:highlighter"
              href={spell.path == null
                ? resolve("/id/[id]", { id: spell.id })
                : resolve("/chambers/[...path]", {
                    path: spell.path.replace(/^chambers\//u, ""),
                  })}>{spell.name}</a
            >
          {:else}
            <span class="truncate font-semibold">{spell.name}</span>
          {/if}
          <span
            class="relative block rounded-full bg-muted block-2 max-sm:col-span-2 max-sm:row-start-2"
            aria-hidden="true"
          >
            <span
              class={cn("absolute inset-y-0 rounded-full", {
                "bg-(--party)": note == null,
                "border border-(--party) bg-(--party)/35": note != null,
              })}
              style:inset-inline-start="{x0 * 100}%"
              style:inline-size="{Math.max(x1 - x0, 0.01) * 100}%"
            ></span>
          </span>
          <span
            class="font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
            >{spell.to == null
              ? note == null
                ? since({ from: spell.from?.slice(0, 4) ?? "" })
                : spell.from?.slice(0, 4)
              : m.person_served_span({
                  from: spell.from?.slice(0, 4) ?? "",
                  to: spell.to.slice(0, 4),
                })}</span
          >
          {#if spell.seats != null && spell.seats.length > 0}
            <span
              class="col-span-full text-xs text-pretty text-muted-foreground"
              >{spell.seats
                .map((seat) => {
                  const span =
                    seat.to == null
                      ? since({ from: seat.from?.slice(0, 4) ?? "" })
                      : m.person_served_span({
                          from: seat.from?.slice(0, 4) ?? "",
                          to: seat.to.slice(0, 4),
                        });
                  const reason = endings[seat.ended ?? ""];
                  return reason == null
                    ? m.person_seat({ place: seat.name, span })
                    : m.person_seat_ended({
                        place: seat.name,
                        span,
                        reason: reason(),
                      });
                })
                .join("; ")}</span
            >
          {/if}
        </li>
      {/each}
    </ul>
  </div>
{/snippet}

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
    <header
      class={cn("col-span-12 grid gap-x-4 bg-card px-3.5 pbs-4 pbe-3.5", {
        "grid-cols-[auto_minmax(0,1fr)] sm:gap-x-5": person.photo != null,
      })}
    >
      {#if person.photo != null}
        <img
          class="row-span-3 aspect-4/5 rounded-sm bg-muted object-cover object-top ring-1 ring-border inline-18 sm:inline-28"
          src={person.photo.src}
          alt={m.photo_alt({ name: person.name })}
          width="256"
          height="320"
        />
      {/if}
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
        {:else if person.isIndependent}
          <span class="font-semibold text-foreground"
            >{m.person_parties_none()}</span
          >
        {/if}
        {#if person.caucus != null}
          <span
            >{m.person_caucus({
              caucus: person.caucus.short ?? person.caucus.label,
            })}</span
          >
        {/if}
        {#if person.born != null}
          <span
            >{person.precision === "day"
              ? m.card_born_date({ date: date(person.born) })
              : m.card_born({ year: person.born.slice(0, 4) })}</span
          >
        {/if}
        {#if person.died != null}
          <span
            >{person.diedPrecision === "day"
              ? m.card_died_date({ date: date(person.died) })
              : m.card_died({ year: person.died.slice(0, 4) })}</span
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
      {#if person.photo != null}
        <a
          class="self-end justify-self-start text-xs text-muted-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={person.photo.source}
          rel="external noopener">{credit(person.photo)}</a
        >
      {/if}
    </header>

    {#if entries.length > 0 || person.served.length > 0 || person.elected.length > 0}
      <section class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5">
        {#if entries.length > 0}
          <p
            class="font-serif text-[clamp(1.0625rem,1.6vw,1.25rem)]/snug text-pretty max-inline-[60ch]"
          >
            {races.length === 1
              ? (won > 0 ? m.person_lede_single_won : m.person_lede_single)({
                  year: String(first),
                })
              : first === last
                ? (advanced > 0
                    ? m.person_lede_advanced_year
                    : m.person_lede_year)({
                    won: count(won),
                    advanced: count(advanced),
                    count: count(races.length),
                    year: String(first),
                  })
                : (advanced > 0 ? m.person_lede_advanced : m.person_lede)({
                    won: count(won),
                    advanced: count(advanced),
                    count: count(races.length),
                    first: String(first),
                    last: String(last),
                  })}
          </p>
        {/if}
        {#if person.served.length > 0}
          {@render spells(m.person_served(), null, person.served)}
        {/if}
        {#if person.elected.length > 0}
          {@render spells(
            m.person_elected(),
            m.person_elected_note(),
            person.elected,
          )}
        {/if}
      </section>
    {/if}

    <Parties lanes={data.parties} isGone={person.isGone} died={person.died} />

    <Career record={person.record} />

    <section class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5">
      <h2 class="text-[0.8125rem] font-bold">{m.person_record()}</h2>
      {#if entries.length === 0}
        <p class="text-sm text-muted-foreground">{m.person_empty()}</p>
      {:else}
        <ol class="grid text-sm">
          {#each isOpen ? entries : entries.slice(0, 12) as entry, index (index)}
            <li
              class="grid grid-cols-[3rem_minmax(0,1fr)_7.5rem] items-baseline gap-x-3 border-be border-border/60 py-2 last:border-be-0 max-sm:grid-cols-[2.75rem_minmax(0,1fr)_4rem]"
            >
              <span
                class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                >{index === 0 || entries[index - 1]?.year !== entry.year
                  ? entry.year
                  : ""}</span
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
                  {#if entry.outcome != null && entry.note != null}
                    <Term
                      class={cn(
                        "ms-1 px-1 text-xs font-semibold whitespace-nowrap",
                        {
                          highlighter: !entry.isMuted,
                          "rounded-xs bg-secondary text-muted-foreground":
                            entry.isMuted,
                        },
                      )}
                      label={OUTCOMES[entry.outcome]?.() ?? entry.outcome}
                      definition={explain(entry.note)}
                    />
                  {:else if entry.outcome != null}
                    <span
                      class={cn(
                        "ms-1 px-1 text-xs font-semibold whitespace-nowrap",
                        {
                          highlighter: !entry.isMuted,
                          "rounded-xs bg-secondary text-muted-foreground":
                            entry.isMuted,
                        },
                      )}>{OUTCOMES[entry.outcome]?.() ?? entry.outcome}</span
                    >
                  {/if}
                </span>
                {#if entry.party != null && entry.party.id !== person.party?.id}
                  <Name
                    class="justify-self-start text-[0.8125rem] text-muted-foreground"
                    label={entry.party.label}
                    color={swatch(entry.party.color, `party:${entry.party.id}`)}
                    party={entry.party.id}
                  />
                {:else if entry.party == null && entry.isIndependent === true && !person.isIndependent}
                  <span
                    class="justify-self-start text-[0.8125rem] text-muted-foreground"
                    >{m.person_parties_none()}</span
                  >
                {/if}
              </span>
              <span class="grid justify-items-end gap-1">
                <span class="font-figures text-[0.8125rem] tabular-nums"
                  >{entry.share == null
                    ? entry.isUnopposed === true
                      ? m.person_unopposed()
                      : "–"
                    : percent(entry.share)}</span
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
                {#if entry.majority != null}
                  <span
                    class="font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                    >{m.person_majority({ count: count(entry.majority) })}</span
                  >
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
        {#if entries.length > 12}
          <button
            type="button"
            class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
            aria-expanded={isOpen}
            onclick={() => (isOpen = !isOpen)}
            >{isOpen
              ? m.person_record_fewer()
              : m.person_record_more({ count: String(entries.length) })}</button
          >
        {/if}
      {/if}
    </section>
  </article>
</main>
