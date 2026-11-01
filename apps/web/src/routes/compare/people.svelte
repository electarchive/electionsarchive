<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import { fits } from "#lib/fits.js";
  import { count, credit, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Helix from "./helix.svelte";
  import Tape from "./tape.svelte";

  type Person = NonNullable<Awaited<PageData["body"]>["people"]>[number];
  type Record = Person["record"][number];

  const { people }: { people: Array<Person> } = $props();

  const WON = new Set(["elected", "won", "advanced"]);
  const DAY = 86_400_000;
  const YEAR = DAY * 365.25;

  const pair = $derived(
    [people[0], people[1]].filter((person) => person != null),
  );
  const names = $derived(pair.map((person) => person.name));
  const colors = $derived(
    pair.map((person) =>
      person.party == null
        ? "var(--foreground)"
        : swatch(person.party.color, person.party.id),
    ),
  );
  const share = (entry: Record) =>
    entry.isUnopposed
      ? 1
      : entry.votes == null || entry.total == null || entry.total === 0
        ? null
        : entry.votes / entry.total;
  const at = (date: string) => {
    const year = Number(date.slice(0, 4));
    return year + (Date.parse(date) - Date.UTC(year, 0, 1)) / DAY / 366;
  };
  const tint = (entry: Record, side: number) =>
    entry.party == null
      ? (colors[side] ?? "var(--foreground)")
      : swatch(entry.party.color, entry.party.id);

  const facts = $derived(
    pair.map((person) => {
      const general = person.record.filter((entry) => !entry.isPrimary);
      let votes = 0;
      let best = 0;
      for (const entry of general) {
        votes += entry.votes ?? 0;
        const value = share(entry);
        if (value != null && !entry.isUnopposed) {
          best = Math.max(best, value);
        }
      }
      const years = person.record.map((entry) => entry.year);
      let tenure = 0;
      for (const term of person.served) {
        if (term.from == null) {
          continue;
        }
        tenure +=
          ((term.to == null ? Date.now() : Date.parse(term.to)) -
            Date.parse(term.from)) /
          YEAR;
      }
      const won = person.record.filter((entry) =>
        WON.has(entry.outcome ?? ""),
      ).length;
      return {
        races: person.record.length,
        won,
        rate: person.record.length === 0 ? 0 : won / person.record.length,
        votes,
        best,
        first: years.length === 0 ? null : Math.min(...years),
        last: years.length === 0 ? null : Math.max(...years),
        offices: new Set(person.served.map((term) => term.name)).size,
        tenure,
      };
    }),
  );

  const meetings = $derived.by(() => {
    const [one, two] = pair;
    if (one == null || two == null) {
      return [];
    }
    const theirs = new Map(
      two.record.flatMap((entry) =>
        entry.path == null ? [] : [[entry.path, entry]],
      ),
    );
    return one.record.flatMap((entry) => {
      const other = entry.path == null ? null : theirs.get(entry.path);
      return other == null
        ? []
        : [{ path: entry.path ?? "", mine: entry, theirs: other }];
    });
  });
  const record = $derived.by(() => {
    let mine = 0;
    let theirs = 0;
    for (const meeting of meetings) {
      const one = share(meeting.mine) ?? 0;
      const two = share(meeting.theirs) ?? 0;
      if (
        WON.has(meeting.mine.outcome ?? "") ||
        (!WON.has(meeting.theirs.outcome ?? "") && one > two)
      ) {
        mine += 1;
      } else {
        theirs += 1;
      }
    }
    return [mine, theirs];
  });

  const rows = $derived.by(() => {
    const merged = new Map<
      string,
      { date: string; sides: [Record | null, Record | null] }
    >();
    for (const [side, person] of pair.entries()) {
      for (const [index, entry] of person.record.entries()) {
        const key = entry.path ?? `${side}:${index}`;
        const row = merged.get(key) ?? {
          date: entry.date,
          sides: [null, null],
        };
        row.sides[side] = entry;
        merged.set(key, row);
      }
    }
    return [...merged]
      .map(([key, row]) => ({ key, ...row }))
      .toSorted((x, y) => y.date.localeCompare(x.date));
  });

  const lanes = $derived(
    pair.map((person, side) => ({
      color: colors[side] ?? "var(--foreground)",
      races: person.record.flatMap((entry, index) => {
        const value = share(entry);
        return value == null
          ? []
          : [
              {
                key: entry.path ?? `${side}:${index}`,
                x: at(entry.date),
                share: value,
                isWin: WON.has(entry.outcome ?? ""),
                isPrimary: entry.isPrimary,
                votes: entry.votes,
                color: tint(entry, side),
              },
            ];
      }),
      terms: person.served.flatMap((term) =>
        term.spans.flatMap((span) =>
          span.parties.flatMap((stint) => {
            const from = stint.from ?? span.from;
            return from == null
              ? []
              : [
                  {
                    from: at(from),
                    to: at(
                      stint.to ??
                        span.to ??
                        new Date().toISOString().slice(0, 10),
                    ),
                    color:
                      stint.party == null
                        ? (colors[side] ?? "var(--foreground)")
                        : swatch(stint.party.color, stint.party.id),
                  },
                ];
          }),
        ),
      ),
    })),
  );

  const links = $derived(
    meetings.flatMap((meeting) => {
      const one = share(meeting.mine);
      const two = share(meeting.theirs);
      if (one == null || two == null) {
        return [];
      }
      const isMine =
        WON.has(meeting.mine.outcome ?? "") ||
        (!WON.has(meeting.theirs.outcome ?? "") && one > two);
      return [
        {
          x: at(meeting.mine.date),
          shares: [one, two] satisfies [number, number],
          color: isMine ? tint(meeting.mine, 0) : tint(meeting.theirs, 1),
        },
      ];
    }),
  );

  const tape = $derived.by(() => {
    const [one, two] = facts;
    if (one == null || two == null) {
      return [];
    }
    const span = (fact: typeof one) =>
      fact.first == null || fact.last == null
        ? "–"
        : fact.first === fact.last
          ? String(fact.first)
          : `${fact.first}–${fact.last}`;
    const length = (fact: typeof one) =>
      fact.first == null || fact.last == null ? 0 : fact.last - fact.first + 1;
    return [
      {
        label: m.versus_tape_races(),
        values: [one.races, two.races],
        text: [count(one.races), count(two.races)],
      },
      {
        label: m.versus_tape_won(),
        values: [one.won, two.won],
        text: [count(one.won), count(two.won)],
      },
      {
        label: m.versus_tape_rate(),
        values: [one.rate, two.rate],
        text: [percent(one.rate, 0), percent(two.rate, 0)],
      },
      {
        label: m.versus_tape_votes(),
        values: [one.votes, two.votes],
        text: [count(one.votes), count(two.votes)],
      },
      {
        label: m.versus_tape_best(),
        values: [one.best, two.best],
        text: [percent(one.best), percent(two.best)],
      },
      {
        label: m.versus_tape_span(),
        values: [length(one), length(two)],
        text: [span(one), span(two)],
      },
      {
        label: m.versus_tape_offices(),
        values: [one.offices, two.offices],
        text: [count(one.offices), count(two.offices)],
      },
      {
        label: m.versus_tape_tenure(),
        values: [one.tenure, two.tenure],
        text: [count(Math.round(one.tenure)), count(Math.round(two.tenure))],
      },
    ] satisfies Array<{
      label: string;
      values: [number, number];
      text: [string, string];
    }>;
  });

  let active = $state<string | null>(null);
  const shown = $derived(rows.find((row) => row.key === active) ?? null);
</script>

{#snippet marker(side: number, color = "var(--foreground)")}
  <span
    class={cn("inline-block shrink-0 block-2 inline-2", {
      "rounded-full": side === 0,
    })}
    style:background-color={color}
    aria-hidden="true"
  ></span>
{/snippet}

{#snippet cell(entry: Record | null, side: number)}
  {#if entry == null}
    <span></span>
  {:else}
    {const value = $derived(share(entry))}
    {const isWin = $derived(WON.has(entry.outcome ?? ""))}
    <span
      class={cn("grid min-inline-0", {
        "justify-items-end text-end": side === 0,
        "opacity-60": entry.isPrimary,
      })}
    >
      <span
        class={cn("flex items-center gap-1.5 min-inline-0", {
          "flex-row-reverse": side === 0,
        })}
      >
        <span
          class={cn("shrink-0 border block-2 inline-2", {
            "rounded-full": side === 0,
          })}
          style:border-color={tint(entry, side)}
          style:background-color={isWin ? tint(entry, side) : "transparent"}
          aria-hidden="true"
        ></span>
        {#if entry.path == null}
          <span class="truncate">{entry.name}</span>
        {:else}
          <a
            class="truncate underline decoration-current/30 underline-offset-2 hover:decoration-current"
            href={resolve("/[...path]", { path: entry.path })}
            title={entry.name}>{entry.name}</a
          >
        {/if}
      </span>
      <span class="font-figures text-xs text-muted-foreground tabular-nums">
        {[
          value == null ? null : percent(value),
          entry.votes == null || entry.isUnopposed ? null : count(entry.votes),
        ]
          .filter((part) => part != null)
          .join(" ")}
      </span>
    </span>
  {/if}
{/snippet}

<section class="col-span-12 grid gap-px bg-border sm:grid-cols-2">
  {#each pair as person, side (person.id)}
    <div
      class={cn("flex items-start gap-3 bg-card px-3.5 py-3", {
        "sm:flex-row-reverse sm:text-end": side === 0,
      })}
    >
      {#if person.photo != null}
        <img
          class="shrink-0 bg-muted object-cover object-top block-20 inline-16"
          src={person.photo.src}
          alt=""
          title={credit(person.photo)}
          width="64"
          height="80"
        />
      {/if}
      <div class="grid gap-1 min-inline-0">
        <a
          class="flex items-center gap-2 text-lg/tight font-[720] hover:underline"
          class:sm:flex-row-reverse={side === 0}
          href={resolve("/id/[id]", { id: person.id })}
        >
          {@render marker(side)}<span class="truncate">{person.name}</span>
        </a>
        {#if person.party != null}
          <span
            class="flex items-center gap-1.5 text-xs"
            class:sm:flex-row-reverse={side === 0}
          >
            {@render marker(1, colors[side])}{person.party.label}
          </span>
        {/if}
        {#if person.born != null}
          <span class="font-figures text-xs text-muted-foreground tabular-nums">
            {person.born.slice(0, 4)}{person.died == null
              ? ""
              : `–${person.died.slice(0, 4)}`}
          </span>
        {/if}
      </div>
    </div>
  {/each}
</section>

<section
  class="col-span-12 grid justify-items-center gap-4 bg-card px-3.5 pbs-4 pbe-5"
>
  <p
    class="text-center text-lg/snug font-semibold text-balance max-inline-[60ch]"
  >
    {meetings.length === 0
      ? m.versus_verdict_people_none({ a: names[0] ?? "", b: names[1] ?? "" })
      : m.versus_verdict_people({
          a: names[0] ?? "",
          b: names[1] ?? "",
          one: count(record[0] ?? 0),
          two: count(record[1] ?? 0),
        })}
  </p>
  <div class="inline-full max-inline-3xl">
    <Tape
      names={[names[0] ?? "", names[1] ?? ""]}
      colors={[colors[0] ?? "", colors[1] ?? ""]}
      rows={tape}
    />
  </div>
</section>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="helix"
  data-export
>
  <div class="grid gap-0.5 pe-10">
    <h2 id="helix" class="heading">{m.versus_helix_title()}</h2>
    <p class="text-xs text-pretty text-muted-foreground max-inline-[100ch]">
      {m.versus_helix_key({ a: names[0] ?? "", b: names[1] ?? "" })}
    </p>
  </div>
  {#if lanes.length === 2}
    <Helix
      lanes={[
        lanes[0] ?? { color: "", races: [], terms: [] },
        lanes[1] ?? { color: "", races: [], terms: [] },
      ]}
      {links}
      bind:active
    >
      {#snippet readout(key)}
        {const row = $derived(rows.find((entry) => entry.key === key))}
        {#if row != null}
          <p class="font-figures font-bold tabular-nums">
            {row.date.slice(0, 4)}
          </p>
          {#each row.sides as entry, side (side)}
            {#if entry != null}
              {const value = $derived(share(entry))}
              <p class="flex items-center gap-1.5">
                {@render marker(side, tint(entry, side))}
                <span class="truncate">{entry.name}</span>
              </p>
              <p class="ps-3.5 font-figures text-muted-foreground tabular-nums">
                {[
                  value == null ? null : percent(value),
                  entry.votes == null ? null : count(entry.votes),
                ]
                  .filter((part) => part != null)
                  .join(" ")}
              </p>
            {/if}
          {/each}
        {/if}
      {/snippet}
    </Helix>
  {/if}
  <Export name="careers" class="absolute inset-e-2 inset-bs-2" />
</section>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="races"
  data-export
>
  <div class="grid gap-0.5 pe-10">
    <h2 id="races" class="heading">{m.versus_races_title()}</h2>
    <p class="text-xs text-pretty text-muted-foreground">
      {m.versus_races_key()}
    </p>
  </div>
  <div
    class="relative overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-144 data-fits:overflow-visible"
    {@attach fits}
  >
    <table class="table-fixed text-[0.8125rem] inline-full">
      <colgroup>
        <col />
        <col class="inline-14" />
        <col />
      </colgroup>
      <thead
        class="sticky inset-bs-0 z-2 bg-background text-xs text-muted-foreground [&_th]:shadow-[inset_0_-1px_0_var(--color-border)]"
      >
        <tr>
          <th class="pe-2 text-end font-semibold block-7">
            <span class="flex items-center justify-end gap-1.5"
              ><span class="truncate">{names[0]}</span>{@render marker(0)}</span
            >
          </th>
          <th class="text-center font-semibold">{m.versus_year()}</th>
          <th class="ps-2 text-start font-semibold">
            <span class="flex items-center gap-1.5"
              >{@render marker(1)}<span class="truncate">{names[1]}</span></span
            >
          </th>
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.key)}
          {const isMet = $derived(row.sides[0] != null && row.sides[1] != null)}
          <tr
            class={cn("border-be border-border", {
              "bg-muted": isMet,
              highlighter: shown?.key === row.key,
            })}
            onpointerenter={() => (active = row.key)}
          >
            <td class="py-1 pe-2">{@render cell(row.sides[0], 0)}</td>
            <td
              class={cn("text-center font-figures tabular-nums", {
                "font-bold": isMet,
              })}>{row.date.slice(0, 4)}</td
            >
            <td class="py-1 ps-2">{@render cell(row.sides[1], 1)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <Export name="races" class="absolute inset-e-2 inset-bs-2" />
</section>
