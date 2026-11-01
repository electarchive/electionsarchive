<script lang="ts">
  import { count, percent, plural, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { Contest } from "./desk.svelte.js";

  const { contest }: { contest: Contest } = $props();

  const ranked = $derived(
    contest.rows
      .filter((row) => row.current != null)
      .toSorted((a, b) => (b.current?.share ?? 0) - (a.current?.share ?? 0)),
  );
  const first = $derived(ranked[0] ?? null);
  const second = $derived(ranked[1] ?? null);
  const isWon = $derived(
    ["won", "elected"].includes(first?.current?.outcome ?? ""),
  );
  const elected = $derived(
    ranked.filter((row) =>
      ["won", "elected"].includes(row.current?.outcome ?? ""),
    ).length,
  );
  const margin = $derived(
    first?.current == null
      ? null
      : {
          share: first.current.share - (second?.current?.share ?? 0),
          votes:
            first.current.votes == null
              ? null
              : first.current.votes - (second?.current?.votes ?? 0),
        },
  );
  const change = $derived(
    first?.current == null || first.earlier == null
      ? null
      : first.current.share - first.earlier.share,
  );
  const turnout = $derived(
    contest.turnout == null || contest.turnout.registered === 0
      ? null
      : {
          share: contest.turnout.voters / contest.turnout.registered,
          earlier:
            contest.turnout.earlier == null ||
            contest.turnout.earlier.registered === 0
              ? null
              : contest.turnout.earlier.voters /
                contest.turnout.earlier.registered,
        },
  );
  const led = $derived.by(() => {
    const places = contest.places;
    if (places == null || first == null || places.rows.length < 2) {
      return null;
    }
    const at = places.legend.findIndex((entry) => entry.key === first.key);
    let found = 0;
    for (const row of places.rows) {
      if (row.leader === at) {
        found += 1;
      }
    }
    return { count: found, of: places.rows.length };
  });
  const seats = $derived(
    contest.chamber == null
      ? null
      : {
          size: contest.chamber.size,
          parties: contest.chamber.parties
            .filter((party) => party.seats > 0)
            .toSorted((a, b) => b.seats - a.seats)
            .slice(0, 4),
        },
  );
  const year = $derived(contest.target?.year ?? null);
  const noun = $derived(
    plural(
      contest.places?.levels.find(
        (level) => level.code === contest.places?.level,
      )?.noun ?? "",
    ),
  );
</script>

{#if first?.current != null}
  {const color = $derived(swatch(first.color, first.key))}
  <div class="grid content-start gap-2.5">
    <div class="grid gap-1">
      <p
        class="flex items-center gap-1.5 text-[0.75em] font-semibold text-muted-foreground"
      >
        <span
          class="rounded-full block-2 inline-2"
          style:background-color={color}
          aria-hidden="true"
        ></span>
        {isWon
          ? (first.current?.seats ?? 0) > 1
            ? m.workbench_summary_won_seats({
                seats: count(first.current?.seats ?? 0),
              })
            : elected > 1
              ? m.workbench_summary_elected({ count: elected })
              : m.outcome_won()
          : m.workbench_summary_leading()}
      </p>
      <p class="flex flex-wrap items-baseline gap-x-2 leading-tight">
        <span class="text-[1.375em] font-[760] text-balance">{first.label}</span
        >
        {#if first.short != null}
          <span class="text-[0.875em] text-muted-foreground">{first.short}</span
          >
        {/if}
      </p>
      <p
        class="flex items-baseline gap-2 font-figures tabular-nums"
        style:color
      >
        <span class="text-[2em] leading-none font-[780]"
          >{percent(first.current.share)}</span
        >
        {#if change != null}
          <span class="text-[0.875em] font-semibold"
            >{m.workbench_summary_change({
              change: points(change),
              year: year ?? "",
            })}</span
          >
        {/if}
      </p>
    </div>
    <dl
      class="grid grid-cols-[repeat(auto-fill,minmax(7.5em,1fr))] gap-x-3 gap-y-2 border-bs border-border/70 pbs-2 text-[0.8125em]"
    >
      {#if margin != null && second != null}
        <div class="grid gap-0.5">
          <dt class="text-[0.875em] text-muted-foreground">
            {m.workbench_summary_over({ name: second.short ?? second.label })}
          </dt>
          <dd
            class="flex flex-wrap items-baseline gap-x-1.5 font-figures font-semibold tabular-nums"
          >
            <span>{points(margin.share)}</span>
            {#if margin.votes != null}
              <span class="text-[0.875em] font-normal text-muted-foreground"
                >{count(margin.votes)}</span
              >
            {/if}
          </dd>
        </div>
      {/if}
      {#if contest.current != null}
        <div class="grid gap-0.5">
          <dt class="text-[0.875em] text-muted-foreground">{m.col_votes()}</dt>
          <dd class="font-figures font-semibold tabular-nums">
            {count(contest.current.valid)}
          </dd>
        </div>
      {/if}
      {#if turnout != null}
        <div class="grid gap-0.5">
          <dt class="text-[0.875em] text-muted-foreground">
            {m.workbench_summary_turnout()}
          </dt>
          <dd
            class="flex flex-wrap items-baseline gap-x-1.5 font-figures font-semibold tabular-nums"
          >
            <span>{percent(turnout.share)}</span>
            {#if turnout.earlier != null}
              <span class="text-[0.875em] font-normal text-muted-foreground"
                >{points(turnout.share - turnout.earlier)}</span
              >
            {/if}
          </dd>
        </div>
      {/if}
      {#if led != null}
        <div class="grid gap-0.5">
          <dt class="text-[0.875em] text-muted-foreground">
            {m.workbench_summary_led()}
          </dt>
          <dd class="font-figures font-semibold tabular-nums">
            {m.workbench_summary_of({
              count: count(led.count),
              total: count(led.of),
              noun,
            })}
          </dd>
        </div>
      {/if}
    </dl>
    {#if seats != null && seats.parties.length > 0}
      <div class="grid gap-1">
        <div
          class="flex overflow-hidden rounded-[1px] bg-accent block-1.5"
          aria-hidden="true"
        >
          {#each seats.parties as party (party.key)}
            <span
              style:inline-size="{(party.seats /
                (seats.size ?? seats.parties[0]?.seats ?? 1)) *
                100}%"
              style:background-color={swatch(party.color, party.key)}
            ></span>
          {/each}
        </div>
        <p
          class="flex flex-wrap gap-x-3 font-figures text-[0.75em] tabular-nums"
        >
          {#each seats.parties as party (party.key)}
            <span class="flex items-center gap-1">
              <span
                class="rounded-[1px] block-2 inline-2"
                style:background-color={swatch(party.color, party.key)}
                aria-hidden="true"
              ></span>
              <span class="font-sans">{party.short ?? party.label}</span>
              <span class="font-semibold">{party.seats}</span>
            </span>
          {/each}
          {#if seats.size != null}
            <span class="text-muted-foreground"
              >{m.workbench_summary_seats({ size: seats.size })}</span
            >
          {/if}
        </p>
      </div>
    {/if}
    {#if contest.partial != null}
      <p class="text-[0.75em] text-muted-foreground">
        {m.workbench_summary_partial({
          held: count(contest.partial.held),
          of: count(contest.partial.of),
          noun,
        })}
      </p>
    {/if}
  </div>
{/if}
