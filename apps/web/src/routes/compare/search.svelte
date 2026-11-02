<script lang="ts">
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import * as Command from "#lib/components/ui/command/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  const {
    kind,
    other,
    onpick,
  }: {
    kind: string | null;
    other: string | null;
    onpick: (id: string) => void;
  } = $props();

  let value = $state("");
  let term = $state("");

  $effect(() => {
    const next = value.trim();
    const timer = setTimeout(() => {
      term = next;
    }, 140);
    return () => clearTimeout(timer);
  });

  const find = createQuery(() => ({
    ...orpc.search.query.queryOptions({ input: { q: term, limit: 30 } }),
    enabled: term !== "",
    placeholderData: keepPreviousData,
    meta: { quiet: true },
  }));

  const hits = $derived(
    (value.trim() === "" ? [] : (find.data ?? [])).filter(
      (hit) =>
        (kind == null
          ? hit.type === "person" ||
            hit.type === "polity" ||
            hit.type === "party"
          : hit.type === kind) && hit.id !== other,
    ),
  );
</script>

<Command.Root shouldFilter={false}>
  <Command.Input
    placeholder={kind === "person"
      ? m.versus_search_person()
      : kind === "polity"
        ? m.versus_search_place()
        : kind === "party"
          ? m.versus_search_party()
          : m.versus_search()}
    bind:value
  />
  <Command.List class="max-block-80">
    {#if value.trim() !== "" && hits.length === 0 && !find.isFetching}
      <p class="p-3 text-sm text-muted-foreground">
        {m.versus_empty()}
      </p>
    {/if}
    {#each hits as hit (hit.id)}
      <Command.Item
        value={hit.id}
        class="grid gap-0 py-1"
        onSelect={() => onpick(hit.id)}
      >
        <span class="truncate font-semibold">{hit.name}</span>
        {#if hit.context != null}
          <span class="truncate text-xs text-muted-foreground"
            >{hit.context}</span
          >
        {/if}
      </Command.Item>
    {/each}
  </Command.List>
</Command.Root>
