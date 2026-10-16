<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id }: { id: string } = $props();

  const OUTCOMES: Record<string, () => string> = {
    elected: m.outcome_elected,
    won: m.outcome_won,
    advanced: m.outcome_advanced,
    tied: m.outcome_tied,
  };

  const query = createQuery(() => ({
    ...orpc.people.get.queryOptions({ input: { id } }),
    meta: { quiet: true },
  }));

  const entries = $derived.by(() => {
    const record = (query.data?.record ?? []).map((entry) => ({
      ...entry,
      share:
        entry.votes == null || entry.total == null || entry.total === 0
          ? null
          : entry.votes / entry.total,
      current:
        entry.path != null &&
        (page.url.pathname === `/${entry.path}` ||
          page.url.pathname.startsWith(`/${entry.path}/`)),
    }));
    const notable = record.filter(
      (entry) =>
        entry.current || entry.outcome != null || (entry.share ?? 0) >= 0.01,
    );
    return notable.length > 0 ? notable : record;
  });
  const shown = $derived(entries.slice(0, 6));
  const hidden = $derived((query.data?.record.length ?? 0) - shown.length);
</script>

{#if query.data == null}
  <p class="p-3 text-[0.8125rem] text-muted-foreground">
    {query.isError ? m.preview_failed() : m.preview_loading()}
  </p>
{:else}
  {const person = $derived(query.data)}
  <div class="grid gap-0.5 px-3 pbs-2.5 pbe-2">
    <p class="font-serif text-[1.0625rem]/tight font-[620]">{person.name}</p>
    <p
      class="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[0.8125rem] text-muted-foreground"
    >
      {#if person.party != null}
        <span class="flex items-baseline gap-1.5">
          <span
            class="shrink-0 self-center rounded-xs block-2 inline-2"
            style:background-color={swatch(
              person.party.color,
              `party:${person.party.id}`,
            )}
          ></span>
          {person.party.label}
        </span>
      {/if}
      {#if person.born != null}
        <span
          >{person.precision === "day"
            ? m.card_born_date({ date: date(person.born) })
            : m.card_born({ year: person.born.slice(0, 4) })}</span
        >
      {/if}
    </p>
  </div>
  <div class="border-bs px-1.5 pbs-2 pbe-1.5">
    <p class="px-1.5 pbe-1 text-xs text-muted-foreground">{m.card_record()}</p>
    {#if shown.length === 0}
      <p class="px-1.5 pbe-1 text-[0.8125rem] text-muted-foreground">
        {m.card_empty()}
      </p>
    {:else}
      <ol class="grid gap-px">
        {#each shown as entry (entry.path ?? entry.name)}
          <li>
            <svelte:element
              this={entry.path == null ? "div" : "a"}
              class={cn(
                "grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-baseline gap-x-2 rounded-sm px-1.5 py-1 text-[0.8125rem]/snug",
                {
                  "hover:bg-secondary": entry.path != null && !entry.current,
                  "bg-secondary": entry.current,
                },
              )}
              href={entry.path == null
                ? undefined
                : resolve("/[...path]", { path: entry.path })}
            >
              <span class="font-mono text-xs text-muted-foreground tabular-nums"
                >{entry.year}</span
              >
              <span class="line-clamp-2">
                {entry.name.replace(/^\d{4}\s+/u, "")}
                {#if entry.outcome != null}
                  <span
                    class="ms-0.5 highlighter px-1 text-[0.6875rem] font-semibold whitespace-nowrap"
                    >{OUTCOMES[entry.outcome]?.() ?? entry.outcome}</span
                  >
                {/if}
              </span>
              <span
                class="grid justify-items-end font-mono text-xs tabular-nums"
              >
                <span>{entry.share == null ? "–" : percent(entry.share)}</span>
                {#if entry.electoral != null}
                  <span class="text-[0.6875rem] text-muted-foreground"
                    >{m.series_electoral({
                      votes: count(entry.electoral.votes),
                      total: count(entry.electoral.total),
                    })}</span
                  >
                {/if}
              </span>
            </svelte:element>
          </li>
        {/each}
      </ol>
      {#if hidden > 0}
        <p class="px-1.5 pbs-1 text-xs text-muted-foreground">
          {m.card_more({ count: count(hidden) })}
        </p>
      {/if}
    {/if}
  </div>
  <div
    class="flex flex-wrap items-center gap-x-4 gap-y-1 border-bs px-3 py-2 text-[0.8125rem]"
  >
    <a
      class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
      href={resolve("/id/[id]", { id })}>{m.card_open()}</a
    >
    {#if person.wikidata != null}
      <a
        class="inline-flex items-center gap-1 underline decoration-border underline-offset-4 hover:decoration-foreground"
        href="https://www.wikidata.org/wiki/{person.wikidata}"
        rel="external noopener"
        >{m.card_wikidata()}<ArrowUpRightIcon class="block-3 inline-3" /></a
      >
    {/if}
  </div>
{/if}
