<script lang="ts">
  import { resolve } from "$app/paths";
  import type { Electors } from "#lib/atlas/data.js";
  import type { tally } from "#lib/atlas/electors.js";
  import Name from "#lib/components/name.svelte";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    electors,
    held,
    tone,
    isCompact = false,
  }: {
    electors: Electors;
    held: NonNullable<ReturnType<typeof tally>>;
    tone: (row: Electors["rows"][number]) => string;
    isCompact?: boolean;
  } = $props();

  const rows = $derived(held.rows.map((row) => ({ ...row, color: tone(row) })));
  const duel = $derived(held.isPaired ? [] : rows.slice(0, 2));
  const rest = $derived(held.isPaired ? [] : rows.slice(2));
  const segments = $derived([
    ...[...duel.slice(0, 1), ...rest].map((row) => ({
      key: row.key,
      color: row.color,
      votes: row.votes,
    })),
    ...(held.absent > 0 ? [{ key: "", color: null, votes: held.absent }] : []),
    ...duel
      .slice(1)
      .map((row) => ({ key: row.key, color: row.color, votes: row.votes })),
  ]);
  const roles = {
    president: m.atlas_electors_president,
    vice: m.atlas_electors_vice,
    elected: m.atlas_electors_elected,
  };
</script>

{#snippet badge(role: keyof typeof roles | null, color: string)}
  {#if role != null}
    <span
      class="shrink-0 rounded-xs px-1.5 text-xs/5 font-semibold text-card"
      style:background={color}>{roles[role]()}</span
    >
  {/if}
{/snippet}

{#if isCompact}
  {const winner = $derived(rows.find((row) => row.role != null) ?? rows[0])}
  {const rival = $derived(rows.find((row) => row !== winner))}
  {#if winner != null}
    <p class="flex items-center gap-2 text-[0.9375rem]">
      <span
        class="shrink-0 rounded-xs block-3 inline-3"
        style:background={winner.color}
      ></span>
      <span class="truncate font-semibold">{winner.label}</span>
      {@render badge(winner.role, winner.color)}
      <span
        class="font-figures font-semibold tabular-nums"
        style:color={winner.color}>{count(winner.votes)}</span
      >
      {#if rival != null}
        <span
          class="flex shrink-4 items-center gap-1.5 text-[0.8125rem] text-muted-foreground min-inline-0"
        >
          <span
            class="font-figures font-semibold tabular-nums"
            style:color={rival.color}>{count(rival.votes)}</span
          >
          <span class="truncate">{rival.label}</span>
        </span>
      {/if}
    </p>
  {/if}
{:else if rows.length > 0}
  <section class="grid grid-cols-[minmax(0,1fr)] gap-2.5">
    <header class="flex items-baseline justify-between gap-3 text-[0.8125rem]">
      <h3 class="font-semibold">{m.electors_title()}</h3>
      <span class="font-figures text-muted-foreground tabular-nums">
        {held.isNational
          ? m.electors_majority({ count: count(electors.majority) })
          : m.atlas_electors_state({ count: count(held.appointed) })}
      </span>
    </header>

    {#if held.isPaired}
      <ul class="grid grid-cols-[minmax(0,1fr)] gap-2">
        {#each rows as row (row.key)}
          <li class="grid grid-cols-[minmax(0,1fr)] gap-1">
            <div class="flex items-center gap-2 text-[0.8125rem]">
              <Name
                class={cn("flex-1 truncate", {
                  "font-semibold": row.role != null,
                })}
                label={row.label}
                color={row.color}
                person={row.person}
                party={row.party}
              />
              {@render badge(row.role, row.color)}
              <span
                class="text-end font-figures font-semibold tabular-nums inline-8"
                >{count(row.votes)}</span
              >
            </div>
            <div class="relative">
              <div class="relative rounded-xs bg-secondary block-1.5">
                <div
                  class="absolute inset-y-0 inset-s-0 rounded-xs"
                  style:background={row.color}
                  style:inline-size="{held.appointed === 0
                    ? 0
                    : Math.min(1, row.votes / held.appointed) * 100}%"
                ></div>
              </div>
              {#if held.isNational}
                <span
                  class="absolute -inset-y-0.5 bg-foreground inline-px"
                  style:inset-inline-start="{(electors.majority /
                    held.appointed) *
                    100}%"
                  aria-hidden="true"
                ></span>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {:else}
      <div class="grid grid-cols-2 gap-3">
        {#each duel as row, index (row.key)}
          <div
            class={cn("grid content-start gap-0.5 min-inline-0", {
              "justify-items-end text-end": index === 1,
            })}
          >
            <div
              class={cn("flex items-center gap-1.5 max-inline-full", {
                "flex-row-reverse": index === 1,
              })}
            >
              <Name
                class="truncate text-[0.8125rem] font-semibold"
                label={row.label}
                color={row.color}
                person={row.person}
                party={row.party}
              />
              {@render badge(row.role, row.color)}
            </div>
            <span
              class="font-figures text-3xl/9 font-[720] tabular-nums"
              style:color={row.color}>{count(row.votes)}</span
            >
            {#if row.pledged != null && row.pledged !== row.votes}
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{m.atlas_electors_pledged({ count: count(row.pledged) })}</span
              >
            {/if}
          </div>
        {/each}
      </div>
      <div class="relative">
        <div
          class="flex gap-px overflow-hidden rounded-xs bg-secondary block-3"
          role="img"
          aria-label={rows
            .map((row) => `${row.label} ${count(row.votes)}`)
            .join(", ")}
        >
          {#each segments as segment (segment.key)}
            <span
              class="block shrink-0 basis-0"
              style:background={segment.color}
              style:flex-grow={segment.votes}
            ></span>
          {/each}
        </div>
        {#if held.isNational}
          <span
            class="absolute -inset-y-1 inset-s-1/2 bg-foreground inline-px"
            aria-hidden="true"
          ></span>
        {/if}
      </div>
      {#if rest.length > 0}
        <ul class="flex flex-wrap gap-x-3 gap-y-1 text-[0.8125rem]">
          {#each rest as row (row.key)}
            <li class="flex items-baseline gap-1.5">
              <Name
                label={row.label}
                color={row.color}
                person={row.person}
                party={row.party}
              />
              <span class="font-figures font-semibold tabular-nums"
                >{count(row.votes)}</span
              >
            </li>
          {/each}
        </ul>
      {/if}
    {/if}

    {#if (held.isNational && held.isPaired) || held.house != null || held.faithless > 0 || held.absent > 0}
      <div
        class="grid gap-1 text-[0.8125rem] text-pretty text-muted-foreground"
      >
        {#if held.isNational && held.isPaired}
          <p>{m.atlas_electors_two()}</p>
        {/if}
        {#if held.faithless > 0}
          <p>{m.atlas_electors_faithless({ count: held.faithless })}</p>
        {/if}
        {#if held.absent > 0}
          <p>{m.atlas_electors_absent({ count: held.absent })}</p>
        {/if}
        {#if held.house != null}
          {const house = $derived(held.house)}
          {const name = $derived(house.label ?? "")}
          {const day = $derived(date(house.date))}
          {const note = $derived(
            house.tie == null
              ? m.atlas_electors_house({ name, date: day })
              : m.atlas_electors_house_tie({ ...house.tie, name, date: day }),
          )}
          {#if house.path == null}
            <p>{note}</p>
          {:else}
            <a
              class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
              href={resolve("/[...path]", { path: house.path })}>{note}</a
            >
          {/if}
        {/if}
      </div>
    {/if}

    {#if held.isNational && electors.path != null}
      <a
        class="justify-self-start text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
        href={resolve("/[...path]", { path: electors.path })}
        >{m.electors_cast({ date: date(electors.date) })}</a
      >
    {/if}
  </section>
{/if}
