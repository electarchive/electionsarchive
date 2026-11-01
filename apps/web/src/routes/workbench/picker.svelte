<script lang="ts">
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import * as Command from "#lib/components/ui/command/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  let {
    open = $bindable(false),
    title,
    quick,
    quickLabel,
    onPick,
  }: {
    open?: boolean;
    title: string;
    quick: Array<{ id: string; label: string; detail: string | null }>;
    quickLabel: string;
    onPick: (id: string) => void;
  } = $props();

  let typed = $state("");
  let term = $state("");

  $effect(() => {
    const next = typed.trim();
    const timer = setTimeout(() => (term = next), 140);
    return () => clearTimeout(timer);
  });

  const found = createQuery(() => ({
    ...orpc.search.query.queryOptions({ input: { q: term, limit: 20 } }),
    enabled: open && term.length > 1,
    placeholderData: keepPreviousData,
  }));
  const hits = $derived(
    term.length > 1
      ? (found.data ?? []).filter((hit) => hit.type === "contest")
      : [],
  );

  function pick(id: string) {
    onPick(id);
    open = false;
    typed = "";
    term = "";
  }
</script>

<Command.Dialog
  bind:open
  {title}
  description={m.workbench_contest_placeholder()}
  shouldFilter={false}
  class="sm:max-inline-xl"
>
  <Command.Input
    bind:value={typed}
    placeholder={m.workbench_contest_placeholder()}
  />
  <Command.List class="max-block-[min(60dvh,28rem)]">
    {#if term.length > 1 && !found.isFetching && hits.length === 0}
      <Command.Empty>{m.workbench_contest_empty()}</Command.Empty>
    {/if}
    {#if hits.length > 0}
      <Command.Group heading={m.workbench_contest()}>
        {#each hits as hit (hit.id)}
          <Command.Item
            value={`hit:${hit.id}`}
            onSelect={() => pick(hit.id)}
            class="grid gap-0.5"
          >
            <span class="truncate">{hit.name}</span>
            <span class="truncate text-xs text-muted-foreground"
              >{hit.context}{#if hit.date != null}, {date(hit.date)}{/if}</span
            >
          </Command.Item>
        {/each}
      </Command.Group>
    {:else if quick.length > 0}
      <Command.Group heading={quickLabel}>
        {#each quick as entry (entry.id)}
          <Command.Item
            value={`quick:${entry.id}`}
            onSelect={() => pick(entry.id)}
            class="grid gap-0.5"
          >
            <span class="truncate">{entry.label}</span>
            {#if entry.detail != null}
              <span class="truncate text-xs text-muted-foreground"
                >{entry.detail}</span
              >
            {/if}
          </Command.Item>
        {/each}
      </Command.Group>
    {/if}
  </Command.List>
</Command.Dialog>
