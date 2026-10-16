<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { on } from "svelte/events";
  import * as Command from "#lib/components/ui/command/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { finder } from "#lib/search.svelte.js";

  const GROUPS: Array<[string, () => string]> = [
    ["polity", m.search_group_polity],
    ["election", m.search_group_election],
    ["contest", m.search_group_contest],
    ["party", m.search_group_party],
    ["person", m.search_group_person],
  ];

  let term = $state("");

  $effect(() => {
    const next = finder.value.trim();
    const timer = setTimeout(() => (term = next), 140);
    return () => clearTimeout(timer);
  });

  $effect(() =>
    on(document, "keydown", (event) => {
      if (
        event.key.toLowerCase() === "k" &&
        (event.metaKey || event.ctrlKey) &&
        !event.altKey
      ) {
        event.preventDefault();
        finder.isOpen = !finder.isOpen;
      }
    }),
  );

  const query = createQuery(() => ({
    ...orpc.search.query.queryOptions({ input: { q: term, limit: 20 } }),
    enabled: finder.isOpen && term !== "",
    placeholderData: keepPreviousData,
    meta: { quiet: true },
  }));

  const groups = $derived.by(() => {
    const hits = term === "" ? [] : (query.data ?? []);
    return GROUPS.map(([type, heading]) => ({
      type,
      heading: heading(),
      first: hits.findIndex((hit) => hit.type === type),
      hits: hits.filter((hit) => hit.type === type && hit.path != null),
    }))
      .filter((group) => group.hits.length > 0)
      .toSorted((a, b) => a.first - b.first);
  });
</script>

<button
  type="button"
  class="flex items-center gap-2 rounded-sm border border-input bg-card ps-2.5 pe-1.5 text-sm text-muted-foreground block-8 hover:bg-secondary hover:text-foreground"
  onclick={() => (finder.isOpen = true)}
>
  <SearchIcon class="block-4 inline-4" />
  <span class="max-sm:sr-only">{m.search_open()}</span>
  <kbd
    class="keycap rounded-xs border border-input px-1 font-mono text-[0.6875rem] max-sm:hidden"
    >⌘K</kbd
  >
</button>

<Command.Dialog
  bind:open={finder.isOpen}
  title={m.search_title()}
  description={m.search_placeholder()}
  shouldFilter={false}
  class="sm:max-inline-xl"
>
  <Command.Input
    placeholder={m.search_placeholder()}
    bind:value={finder.value}
  />
  <Command.List class="max-block-[min(28rem,60dvh)]">
    {#if finder.value.trim() === ""}
      <p class="px-3 py-4 text-sm text-pretty text-muted-foreground">
        {m.search_hint()}
      </p>
    {:else if groups.length === 0}
      <p class="px-3 py-4 text-sm text-muted-foreground">
        {query.isFetching || term !== finder.value.trim()
          ? m.search_loading()
          : m.search_empty({ query: finder.value.trim() })}
      </p>
    {/if}
    {#each groups as group (group.type)}
      <Command.Group heading={group.heading}>
        {#each group.hits as hit (hit.id)}
          <Command.Item
            value={hit.id}
            class="grid gap-0 py-1"
            onSelect={() => {
              finder.isOpen = false;
              finder.value = "";
              if (hit.path != null) {
                void goto(resolve("/[...path]", { path: hit.path }));
              }
            }}
          >
            <span class="truncate font-semibold">{hit.name}</span>
            {#if hit.context != null || hit.date != null}
              <span class="truncate text-xs text-muted-foreground">
                {[
                  hit.type === "person" && hit.context != null
                    ? m.search_person_latest({ contest: hit.context })
                    : hit.context,
                  hit.date == null ? null : date(hit.date),
                ]
                  .filter((part) => part != null)
                  .join(", ")}
              </span>
            {/if}
          </Command.Item>
        {/each}
      </Command.Group>
    {/each}
  </Command.List>
</Command.Dialog>
