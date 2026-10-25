<script lang="ts">
  import { resolve } from "$app/paths";
  import { SvelteSet } from "svelte/reactivity";
  import Name from "#lib/components/name.svelte";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    council,
    hasTenures,
  }: { council: NonNullable<PageData["council"]>; hasTenures: boolean } =
    $props();

  const uid = $props.id();

  const opened = new SvelteSet<string>();

  const parties = $derived(
    new Map(council.parties.map((party) => [party.id, party])),
  );
  const color = (party: string | null) =>
    !council.isPartisan || party == null
      ? null
      : swatch(parties.get(party)?.color ?? null, `party:${party}`);
  const current = $derived(council.seats.filter((seat) => seat.isCurrent));
  const regular = $derived(
    council.elections.filter((election) => !election.isMinor),
  );
  const latest = $derived(regular[0]?.date ?? "");
  const people = $derived(
    new Set(
      council.seats.flatMap((seat) =>
        seat.holders.map((holder) => holder.person ?? holder.name),
      ),
    ).size,
  );
  const year = (value: string | null) => value?.slice(0, 4) ?? "";
  const span = (from: string, to: string | null) =>
    to == null
      ? m.polity_span({ first: year(from), last: m.governments_present() })
      : year(from) === year(to)
        ? year(from)
        : m.polity_span({ first: year(from), last: year(to) });
  const toggle = (key: string) => {
    if (opened.has(key)) {
      opened.delete(key);
    } else {
      opened.add(key);
    }
  };
</script>

{#snippet more(key: string, total: number)}
  <button
    type="button"
    class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
    aria-expanded={opened.has(key)}
    onclick={() => toggle(key)}
    >{opened.has(key)
      ? m.regions_fewer()
      : m.regions_more({ count: count(total) })}</button
  >
{/snippet}

{#if !council.isPartisan}
  <dl
    class="col-span-12 flex flex-wrap items-baseline gap-x-7 gap-y-1.5 bg-card px-3.5 py-3"
  >
    <div class="flex items-baseline gap-2">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {m.chamber_seats()}
      </dt>
      <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
        {count(council.sizes.at(-1)?.seats ?? current.length)}
      </dd>
    </div>
    <div class="flex items-baseline gap-2">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {m.council_elections()}
      </dt>
      <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
        {count(regular.length)}
        <span class="text-sm text-muted-foreground"
          >{span(regular.at(-1)?.date ?? latest, latest)}</span
        >
      </dd>
    </div>
    <div class="flex items-baseline gap-2">
      <dt class="text-[0.8125rem] text-muted-foreground">
        {m.council_people()}
      </dt>
      <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
        {count(people)}
      </dd>
    </div>
  </dl>
{/if}

<section
  class="col-span-12 grid content-start gap-3 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="{uid}-members"
>
  <div class="grid gap-1">
    <h2 id="{uid}-members" class="text-lg/tight font-[740] tracking-[-0.012em]">
      {m.council_members_title()}
    </h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {m.council_members_lede()}
    </p>
  </div>
  <ul class="columns-[22rem] gap-x-8 text-sm">
    {#each current as seat (seat.key)}
      {const holder = $derived(seat.holders.at(-1))}
      {#if holder != null}
        <li
          class="grid break-inside-avoid grid-cols-[5.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 py-1.5 pointer-coarse:py-2"
        >
          <span class="text-[0.8125rem] text-muted-foreground"
            >{seat.label}</span
          >
          <span class="flex flex-wrap items-baseline gap-x-2">
            <Name
              class="font-semibold"
              label={holder.name}
              person={holder.person}
              color={color(holder.party)}
            />
            {#if holder.wins === 1 && holder.from >= latest}
              <span class="highlighter px-1 text-[0.6875rem] font-semibold"
                >{m.council_new()}</span
              >
            {/if}
          </span>
          <span
            class="text-end font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
            >{year(holder.from)} · {m.council_wins({
              count: holder.wins,
            })}</span
          >
        </li>
      {/if}
    {/each}
  </ul>
</section>

<section
  class="col-span-12 grid content-start gap-3 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="{uid}-changes"
>
  <div class="grid gap-1">
    <h2 id="{uid}-changes" class="text-lg/tight font-[740] tracking-[-0.012em]">
      {m.council_changes_title()}
    </h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {m.council_changes_lede()}
    </p>
  </div>
  <ol class="grid gap-3">
    {#each opened.has("elections") ? council.elections : council.elections.slice(0, 8) as election (election.date + election.name)}
      {const key = $derived(`changes:${election.date}:${election.name}`)}
      <li class="grid gap-1.5 border-bs border-border pbs-2">
        <p class="flex flex-wrap items-baseline gap-x-2 text-sm">
          {#if election.path == null}
            <span class="font-semibold">{election.name}</span>
          {:else}
            <a
              class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/[...path]", { path: election.path })}
              >{election.name}</a
            >
          {/if}
          <span
            class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
            >{date(election.date)}</span
          >
          {#if election.isSpecial}
            <span class="text-xs text-muted-foreground"
              >{m.council_special()}</span
            >
          {/if}
        </p>
        <dl
          class="flex flex-wrap gap-x-5 gap-y-1 text-[0.8125rem] text-muted-foreground"
        >
          {#each [{ label: m.council_col_filled(), value: election.up }, { label: m.council_col_held(), value: election.held }, { label: m.council_col_open(), value: election.open }, { label: m.council_col_defeated(), value: election.defeated }, { label: m.council_col_runoffs(), value: election.runoffs }] as stat (stat.label)}
            <div class="flex items-baseline gap-1.5">
              <dt>{stat.label}</dt>
              <dd
                class="font-figures font-semibold text-foreground tabular-nums"
              >
                {count(stat.value)}
              </dd>
            </div>
          {/each}
        </dl>
        {#if election.changes.length > 0}
          {const isOpen = $derived(
            opened.has(key) || election.changes.length <= 4,
          )}
          <ul
            class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] gap-x-6 text-[0.8125rem]"
          >
            {#each isOpen ? election.changes : election.changes.slice(0, 4) as change, index (index)}
              <li
                class="grid grid-cols-[minmax(4.5rem,auto)_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1 pointer-coarse:py-1.5"
              >
                {#if change.path == null}
                  <span class="text-muted-foreground">{change.seat}</span>
                {:else}
                  <a
                    class="text-muted-foreground underline decoration-border underline-offset-2 hover:decoration-foreground"
                    href={resolve("/[...path]", { path: change.path })}
                    >{change.seat}</a
                  >
                {/if}
                <span
                  class={cn({ "font-semibold": change.kind === "defeated" })}
                >
                  {#if change.kind === "new" || change.previous == null}
                    {m.council_change_new({ winner: change.winner.name })}
                  {:else if change.kind === "defeated"}
                    {m.council_change_defeated({
                      winner: change.winner.name,
                      previous: change.previous.name,
                    })}
                  {:else}
                    {m.council_change_open({
                      winner: change.winner.name,
                      previous: change.previous.name,
                    })}
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
          {#if election.changes.length > 4}
            {@render more(key, election.changes.length)}
          {/if}
        {/if}
      </li>
    {/each}
  </ol>
  {#if council.elections.length > 8}
    {@render more("elections", council.elections.length)}
  {/if}
</section>

<section
  class="col-span-12 grid content-start gap-3 bg-card px-3.5 pbs-4 pbe-4"
  aria-labelledby="{uid}-holders"
>
  <div class="grid gap-1">
    <h2 id="{uid}-holders" class="text-lg/tight font-[740] tracking-[-0.012em]">
      {m.council_holders_title()}
    </h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {m.council_holders_lede()}
      {#if !hasTenures}
        {m.council_tenures_note()}
      {/if}
    </p>
  </div>
  <div class="columns-[20rem] gap-x-8">
    {#each opened.has("holders") ? council.seats : council.seats.slice(0, 12) as seat (seat.key)}
      <div class="grid break-inside-avoid content-start gap-1 pbe-4">
        <h3
          class={cn("text-sm font-semibold", {
            "text-muted-foreground": !seat.isCurrent,
          })}
        >
          {seat.label}
        </h3>
        <ol class="grid text-[0.8125rem]">
          {#each seat.holders.toReversed() as holder, index (index)}
            <li
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 py-1 last:border-be-0 pointer-coarse:py-1.5"
            >
              <span class="flex flex-wrap items-baseline gap-x-2">
                <Name
                  label={holder.name}
                  person={holder.person}
                  color={color(holder.party)}
                />
                {#if holder.via === "special"}
                  <span class="text-xs text-muted-foreground"
                    >{m.council_special()}</span
                  >
                {/if}
              </span>
              <span class="font-figures text-muted-foreground tabular-nums"
                >{span(holder.from, holder.until)}
                {#if holder.wins > 1}
                  · {m.council_wins({ count: holder.wins })}
                {/if}</span
              >
            </li>
          {/each}
        </ol>
      </div>
    {/each}
  </div>
  {#if council.seats.length > 12}
    {@render more("holders", council.seats.length)}
  {/if}
</section>

{#if council.sizes.length > 0}
  <section
    class="col-span-12 grid content-start gap-2 bg-card px-3.5 pbs-4 pbe-4"
    aria-labelledby="{uid}-sizes"
  >
    <h2 id="{uid}-sizes" class="text-lg/tight font-[740] tracking-[-0.012em]">
      {m.council_sizes_title()}
    </h2>
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {m.council_sizes_lede({ year: year(council.sizes[0]?.from ?? null) })}
    </p>
    <ol class="flex flex-wrap gap-x-6 gap-y-1 text-sm">
      {#each council.sizes as size (size.from)}
        <li class="font-figures tabular-nums">
          {m.council_size_entry({
            seats: count(size.seats),
            year: year(size.from),
          })}
        </li>
      {/each}
    </ol>
  </section>
{/if}
