<script lang="ts">
  import { Await, peek } from "svelte-islands";
  import * as HoverCard from "#lib/components/ui/hover-card/index.js";
  import { count, date, percent } from "#lib/format.js";
  import { LOST, MEASURES, OUTCOMES } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  let {
    open = $bindable(false),
    anchor,
    data,
    failed,
    onenter,
    onleave,
  }: {
    open?: boolean;
    anchor: HTMLAnchorElement | null;
    data: PageData | null;
    failed: boolean;
    onenter: () => void;
    onleave: () => void;
  } = $props();
</script>

{#snippet list(
  heading: string,
  entries: Array<{ name: string; date?: string | null }>,
)}
  <div class="grid gap-1 border-bs px-3 py-2">
    <p class="text-xs text-muted-foreground">{heading}</p>
    <ul class="grid gap-0.5 text-[0.8125rem]/snug">
      {#each entries.slice(0, 5) as entry, index (index)}
        <li class="flex items-baseline justify-between gap-3">
          <span class="line-clamp-1">{entry.name}</span>
          {#if entry.date != null}
            <span
              class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
              >{entry.date.slice(0, 4)}</span
            >
          {/if}
        </li>
      {/each}
    </ul>
    {#if entries.length > 5}
      <p class="text-xs text-muted-foreground">
        {m.card_more({ count: count(entries.length - 5) })}
      </p>
    {/if}
  </div>
{/snippet}

<HoverCard.Root bind:open>
  <HoverCard.Content
    customAnchor={anchor}
    collisionPadding={8}
    align="start"
    class="p-0 text-start font-sans text-sm font-normal tracking-normal text-foreground normal-case inline-[min(20rem,calc(100vw-2rem))]"
    onpointerenter={onenter}
    onpointerleave={onleave}
  >
    {#if data == null}
      <p class="p-3 text-[0.8125rem] text-muted-foreground">
        {failed ? m.preview_failed() : m.preview_loading()}
      </p>
    {:else if data.kind === "contest"}
      {const head = $derived(data.head)}
      <div class="grid gap-0.5 px-3 pbs-2.5 pbe-2">
        <p class="text-[1.0625rem]/tight font-[720] text-balance">
          {head.scope.noun == null
            ? head.name
            : head.scope.abroad == null
              ? head.scope.name
              : m.scope_abroad_title({ place: head.scope.name })}
        </p>
        <p class="text-[0.8125rem] text-muted-foreground">
          {head.scope.noun == null ? date(head.election.date) : head.name}
        </p>
      </div>
      <Await promise={data.body} name="preview.contest" key={data.id}>
        {#snippet children(opened)}
          {const contest = $derived(opened.contest)}
          {const rows = $derived(
            contest.rows
              .filter((row) => row.current != null)
              .toSorted(
                (a, b) => (b.current?.votes ?? 0) - (a.current?.votes ?? 0),
              )
              .slice(0, 3),
          )}
          {#if rows.length > 0}
            <ol class="grid gap-2 border-bs px-3 py-2.5">
              {#each rows as row (row.key)}
                <li
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1 text-[0.8125rem]"
                  style:--party={swatch(row.color, row.key)}
                >
                  <span class="flex items-baseline gap-1.5 min-inline-0">
                    <span
                      class="shrink-0 self-center rounded-xs bg-(--party) chip block-2 inline-2"
                    ></span>
                    <span class="truncate font-semibold">{row.label}</span>
                    {#if row.current?.outcome != null}
                      <span
                        class={cn(
                          "shrink-0 px-1 text-[0.6875rem] font-semibold",
                          {
                            highlighter: !LOST.has(row.current.outcome),
                            "text-muted-foreground": LOST.has(
                              row.current.outcome,
                            ),
                          },
                        )}
                        >{OUTCOMES[row.current.outcome]?.() ??
                          row.current.outcome}</span
                      >
                    {/if}
                  </span>
                  <span class="font-figures text-xs tabular-nums"
                    >{contest.current?.isTally === true
                      ? count(row.current?.votes ?? 0)
                      : percent(row.current?.share ?? 0)}</span
                  >
                  <span
                    class="col-span-2 flex overflow-hidden rounded-full bg-muted block-1"
                    aria-hidden="true"
                  >
                    <span
                      class="bg-(--party)"
                      style:inline-size="{(row.current?.share ?? 0) * 100}%"
                    ></span>
                  </span>
                </li>
              {/each}
            </ol>
          {/if}
          {#if contest.current != null}
            <p
              class="flex items-baseline justify-between gap-3 border-bs px-3 py-2 text-xs text-muted-foreground"
            >
              {MEASURES[contest.current.measure]?.() ?? m.stat_valid()}
              <span
                class="font-figures text-[0.8125rem] text-foreground tabular-nums"
                >{count(contest.current.valid)}</span
              >
            </p>
          {/if}
        {/snippet}
      </Await>
    {:else if data.kind === "election"}
      <div class="grid gap-0.5 px-3 pbs-2.5 pbe-2">
        <p class="text-[1.0625rem]/tight font-[720] text-balance">
          {data.election.name}
        </p>
        {#if data.election.date != null}
          <p class="text-[0.8125rem] text-muted-foreground">
            {date(data.election.date)}
          </p>
        {/if}
      </div>
      {const contests = $derived(
        data.election.contests == null
          ? []
          : (peek(data.election.contests)?.value ?? []),
      )}
      {#if contests.length > 0}
        {@render list(m.election_contests(), contests)}
      {/if}
    {:else if data.kind === "polity"}
      <div class="px-3 pbs-2.5 pbe-2">
        <p class="text-[1.0625rem]/tight font-[720] text-balance">
          {data.polity.name}
        </p>
      </div>
      {const elections = $derived(peek(data.polity.elections)?.value ?? [])}
      {#if elections.length > 0}
        {@render list(m.polity_elections(), elections)}
      {/if}
    {/if}
  </HoverCard.Content>
</HoverCard.Root>
