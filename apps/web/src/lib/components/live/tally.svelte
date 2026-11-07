<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import type { Band } from "#lib/live/format.js";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { lead, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Counter from "./counter.svelte";
  import Face from "./face.svelte";

  const LIMIT = 8;

  const {
    options,
    now,
    estimate,
    band = null,
    seats = 1,
    isWaiting,
    isLater = false,
    isRanked = false,
    updated = null,
    rule = null,
    needs = [],
  }: {
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    band?: Band | null;
    seats?: number;
    isWaiting: boolean;
    isLater?: boolean;
    isRanked?: boolean;
    updated?: string | null;
    rule?: { share: number; only: string | null } | null;
    needs?: Array<string>;
  } = $props();

  let isOpen = $state(false);

  const isAnswers = $derived(options.some((option) => option.isAnswer));
  const isPartisan = $derived(
    options.some((option) => option.party != null && option.isAnswer !== true),
  );
  const hasFaces = $derived(
    options.some((option) => option.photo != null || option.person != null),
  );
  const rows = $derived(
    (isWaiting
      ? options.flatMap((option, index) => (option.isWriteIn ? [] : [index]))
      : now.order.filter(
          (index) =>
            options[index]?.isWriteIn !== true || (now.totals[index] ?? 0) > 0,
        )
    ).flatMap((index, rank) => {
      const option = options[index];
      if (option == null) {
        return [];
      }
      const votes = now.totals[index] ?? 0;
      const isCalled = band?.ids.includes(option.id) === true;
      return [
        {
          option,
          rank,
          votes,
          share: now.total > 0 ? votes / now.total : 0,
          isCalled,
          isAhead:
            !isWaiting &&
            (band == null ? now.total > 0 && rank < seats : isCalled),
        },
      ];
    }),
  );
  const keep = $derived.by(() => {
    let last = Math.max(LIMIT, seats + 1);
    for (const row of rows) {
      if (row.isCalled || row.option.isIncumbent) {
        last = Math.max(last, row.rank + 1);
      }
    }
    return last;
  });
  const isFolded = $derived(rows.length > keep + 2);
  const shown = $derived(isFolded && !isOpen ? rows.slice(0, keep) : rows);
  const scale = $derived(
    rule == null ? Math.max(0.01, ...rows.map((row) => row.share)) : 1,
  );
  const gap = $derived.by(() => {
    const [first, second] = rows;
    if (isWaiting || seats > 1 || first == null || second == null) {
      return null;
    }
    const votes = first.votes - second.votes;
    return votes > 0 ? { votes, margin: votes / now.total } : null;
  });
  const shareIn = $derived(
    estimate?.status === "estimating" ||
      estimate?.status === "ranked" ||
      estimate?.status === "settled"
      ? Math.min(1, estimate.shareIn.mid)
      : null,
  );
  const hasPrecincts = $derived(
    now.reporting != null && now.expected != null && now.expected > 0,
  );
</script>

{#snippet call(row: (typeof rows)[number])}
  {#if row.isCalled && !(band?.isUnopposed ?? false)}
    {#if band?.isProjected === true}
      <CircleDashedCheckIcon
        class="shrink-0 block-4 inline-4"
        aria-label={m.night_band_projected()}
      />
    {:else}
      <CircleCheckIcon
        class="shrink-0 block-4 inline-4"
        aria-label={m.live_elected()}
      />
    {/if}
  {/if}
{/snippet}

<section
  class="@container grid gap-3 max-inline-4xl"
  aria-label={m.night_list_title()}
>
  <div
    class="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 text-[0.8125rem] text-muted-foreground"
  >
    {#if updated != null}
      <p class="font-figures tabular-nums">
        {m.night_updated({ time: updated })}
      </p>
    {/if}
    {#if !isLater}
      <div
        class="ms-auto grid justify-items-end gap-0.5 font-figures tabular-nums"
      >
        <span class="flex items-center gap-3">
          {#if shareIn != null}
            <span
              class="relative overflow-hidden rounded-full bg-muted block-1.5 inline-24 sm:inline-32"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0 rounded-full bg-foreground transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
                style:inline-size="{shareIn * 100}%"
              ></span>
            </span>
          {/if}
          <span class="font-semibold text-foreground">
            {#if isWaiting}
              {m.night_counted_none()}
            {:else if shareIn != null}
              {m.night_counted_expected({
                share: percent(Math.min(0.99, shareIn), 0),
              })}
            {:else}
              {m.night_votes({ count: count(now.total) })}
            {/if}
          </span>
        </span>
        {#if hasPrecincts}
          <span class="text-xs">
            {m.night_precincts({
              reporting: count(now.reporting ?? 0),
              expected: count(now.expected ?? 0),
            })}
          </span>
        {/if}
      </div>
    {/if}
  </div>

  <div class="border-bs-2 border-foreground">
    <div
      class={cn(
        "sheet border-be border-border py-2 text-xs font-semibold text-muted-foreground",
        { faced: hasFaces, flat: !isPartisan },
      )}
      aria-hidden="true"
    >
      <span class="[grid-area:name]"
        >{isAnswers ? m.col_answer() : m.col_candidate()}</span
      >
      <span class="text-end [grid-area:votes]"
        >{isRanked ? m.night_list_first() : m.col_votes()}</span
      >
      <span class="text-end [grid-area:share]">{m.col_share()}</span>
    </div>
    <ol>
      {#each shown as row (row.option.id)}
        {#if seats > 1 && row.rank === seats && rows.length > seats}
          <li
            class="flex items-center gap-2 py-1.5 text-xs text-muted-foreground"
            aria-hidden="true"
          >
            <span class="flex-1 border-bs border-dashed border-foreground/40"
            ></span>
            {m.night_seats_line({ count: count(seats) })}
            <span class="flex-1 border-bs border-dashed border-foreground/40"
            ></span>
          </li>
        {/if}
        {const color = $derived(tone(row.option))}
        <li
          class={cn("sheet border-s-4 border-be border-border py-2.5 ps-3", {
            faced: hasFaces,
            flat: !isPartisan,
          })}
          style:border-inline-start-color={color}
        >
          {#if hasFaces}
            <span class="self-center [grid-area:face]">
              <Face
                option={row.option}
                {color}
                class="block-9 inline-9 @lg:block-11 @lg:inline-11"
              />
            </span>
          {/if}
          <span
            class={cn(
              "flex items-center gap-1.5 [grid-area:name] min-inline-0",
              { "self-end": isPartisan, "self-center": !isPartisan },
            )}
          >
            <Name
              label={row.option.label}
              person={row.option.person}
              hasSwatch={false}
              class={cn("truncate text-base/tight tracking-tight", {
                "font-[740]": row.isAhead,
                "font-medium": !row.isAhead,
              })}
            />
            {@render call(row)}
            {#if row.option.isIncumbent}
              <span
                class="shrink-0 rounded-xs bg-muted px-1 text-[0.6875rem]/4 font-semibold text-muted-foreground"
                >{m.results_incumbent()}</span
              >
            {/if}
          </span>
          {#if isPartisan}
            <span
              class="self-start truncate text-[0.8125rem] text-muted-foreground [grid-area:party]"
              >{#if row.option.party == null}{m.night_no_party()}{:else}<Name
                  label={row.option.party.name}
                  party={row.option.party.id}
                  color={row.option.party.color}
                  hasSwatch={false}
                />{/if}</span
            >
          {/if}
          {#if isWaiting}
            <span
              class="self-center text-end font-figures text-lg text-muted-foreground/60 [grid-area:share]"
              aria-hidden="true">–</span
            >
          {:else}
            <Counter
              value={row.votes}
              format={(value: number) => count(Math.round(value))}
              class="self-center text-end text-[0.8125rem] text-muted-foreground [grid-area:votes] @lg:text-[0.9375rem] @lg:text-foreground"
            />
            <Counter
              value={row.share}
              format={(value: number) => percent(value)}
              class={cn(
                "self-center text-end text-lg/tight tracking-tight [grid-area:share]",
                { "font-[780]": row.isAhead, "font-semibold": !row.isAhead },
              )}
            />
          {/if}
          <span
            class="relative self-center overflow-hidden rounded-full bg-muted [grid-area:bar] block-1.5 @lg:block-2.5"
            aria-hidden="true"
          >
            <span
              class="absolute inset-y-0 inset-s-0 rounded-full transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
              style:inline-size="{(row.share / scale) * 100}%"
              style:background-color={color}
            ></span>
            {#if rule != null && (rule.only == null || rule.only === row.option.id)}
              <span
                class="absolute inset-y-0 -translate-x-1/2 bg-foreground inline-0.5"
                style:inset-inline-start="{rule.share * 100}%"
              ></span>
            {/if}
          </span>
        </li>
      {/each}
    </ol>
    {#if isFolded}
      <button
        type="button"
        class="-mx-1 mbs-2 rounded-xs px-1 text-[0.8125rem] font-semibold underline underline-offset-2 hover:highlighter"
        aria-expanded={isOpen}
        onclick={() => (isOpen = !isOpen)}
      >
        {isOpen
          ? m.night_list_fewer()
          : m.night_list_more({ count: count(rows.length) })}
      </button>
    {/if}
    {#if !isWaiting}
      <dl class="grid pbs-1 text-[0.8125rem] text-muted-foreground">
        <div class={cn("sheet py-1.5", { faced: hasFaces, flat: !isPartisan })}>
          <dt class="[grid-area:name]">{m.night_list_total()}</dt>
          <dd
            class="text-end font-figures text-foreground tabular-nums [grid-area:share] @lg:[grid-area:votes]"
          >
            {count(now.total)}
          </dd>
        </div>
        {#if gap != null}
          <div
            class={cn("sheet py-1.5", { faced: hasFaces, flat: !isPartisan })}
          >
            <dt class="[grid-area:name]">{m.col_margin()}</dt>
            <dd
              class="text-end font-figures text-foreground tabular-nums [grid-area:votes]"
            >
              {count(gap.votes)}
            </dd>
            <dd
              class="text-end font-figures font-semibold text-foreground tabular-nums [grid-area:share]"
            >
              {m.night_list_points({ points: lead(gap.margin) })}
            </dd>
          </div>
        {/if}
      </dl>
    {/if}
  </div>
  {#if needs.length > 0}
    <p class="text-[0.8125rem] text-muted-foreground">{needs.join(". ")}</p>
  {/if}
</section>

<style>
  .sheet {
    display: grid;
    column-gap: 0.75rem;
    row-gap: 0.125rem;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "name share"
      "party votes"
      "bar bar";
  }

  .sheet.faced {
    grid-template-columns: 2.25rem minmax(0, 1fr) auto;
    grid-template-areas:
      "face name share"
      "face party votes"
      "face bar bar";
  }

  .sheet.flat {
    grid-template-areas:
      "name share"
      "name votes"
      "bar bar";
  }

  .sheet.faced.flat {
    grid-template-areas:
      "face name share"
      "face name votes"
      "face bar bar";
  }

  @container (min-width: 32rem) {
    .sheet {
      column-gap: 1.25rem;
      grid-template-columns:
        minmax(0, 1fr) minmax(5.5rem, auto) 4.75rem
        minmax(6rem, 16rem);
      grid-template-areas:
        "name votes share bar"
        "party votes share bar";
    }

    .sheet.faced {
      grid-template-columns:
        2.75rem minmax(0, 1fr) minmax(5.5rem, auto) 4.75rem
        minmax(6rem, 16rem);
      grid-template-areas:
        "face name votes share bar"
        "face party votes share bar";
    }

    .sheet.flat {
      grid-template-areas:
        "name votes share bar"
        "name votes share bar";
    }

    .sheet.faced.flat {
      grid-template-areas:
        "face name votes share bar"
        "face name votes share bar";
    }
  }
</style>
