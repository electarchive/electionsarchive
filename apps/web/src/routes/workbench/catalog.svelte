<script lang="ts">
  import SearchIcon from "@lucide/svelte/icons/search";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { Kind } from "./grid.js";
  import Glyph from "./glyph.svelte";
  import { KIND_TEXT } from "./grid.js";

  let {
    open = $bindable(false),
    kinds,
    onPick,
  }: {
    open?: boolean;
    kinds: Array<Kind>;
    onPick: (kind: Kind) => void;
  } = $props();

  const GROUPS: Array<{ name: () => string; kinds: Array<Kind> }> = [
    { name: m.workbench_group_results, kinds: ["summary", "bar", "hemicycle"] },
    { name: m.workbench_group_places, kinds: ["map", "place", "table"] },
    { name: m.workbench_group_lists, kinds: ["closest", "movers", "flips"] },
    { name: m.workbench_group_patterns, kinds: ["scatter", "strip", "note"] },
  ];

  let query = $state("");

  const groups = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    const found: Array<{ name: string; kinds: Array<Kind> }> = [];
    for (const group of GROUPS) {
      const matched = group.kinds.filter(
        (kind) =>
          kinds.includes(kind) &&
          (needle === "" ||
            `${KIND_TEXT[kind].name()} ${KIND_TEXT[kind].about()}`
              .toLocaleLowerCase()
              .includes(needle)),
      );
      if (matched.length > 0) {
        found.push({ name: group.name(), kinds: matched });
      }
    }
    return found;
  });

  function pick(kind: Kind) {
    onPick(kind);
    query = "";
  }
</script>

<Dialog.Root
  bind:open
  onOpenChange={(next: boolean) => {
    if (!next) {
      query = "";
    }
  }}
>
  <Dialog.Content class="gap-3 p-4 sm:max-inline-3xl">
    <Dialog.Header>
      <Dialog.Title>{m.board_add_title()}</Dialog.Title>
      <Dialog.Description>{m.board_add_hint()}</Dialog.Description>
    </Dialog.Header>
    <label
      class="flex items-center gap-2 rounded-sm border border-input px-2.5 block-9 focus-within:ring-2 focus-within:ring-ring/40"
    >
      <SearchIcon
        class="shrink-0 text-muted-foreground block-4 inline-4"
        aria-hidden="true"
      />
      <input
        bind:value={query}
        class="flex-1 bg-transparent text-sm outline-none min-inline-0"
        placeholder={m.board_catalog_search()}
        aria-label={m.board_catalog_search()}
        onkeydown={(event) => {
          const first = groups[0]?.kinds[0];
          if (event.key === "Enter" && first != null) {
            event.preventDefault();
            pick(first);
          }
        }}
      />
    </label>
    <div class="grid gap-3 overflow-y-auto max-block-[60dvh]">
      {#each groups as group (group.name)}
        <section class="grid gap-1">
          <h3 class="text-xs font-semibold text-muted-foreground">
            {group.name}
          </h3>
          <ul class="grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {#each group.kinds as kind (kind)}
              <li>
                <button
                  type="button"
                  class="flex items-start gap-2.5 rounded-sm border border-border p-2 text-start block-full inline-full hover:border-foreground/40 hover:bg-muted focus-visible:bg-muted"
                  onclick={() => pick(kind)}
                >
                  <Glyph {kind} />
                  <span class="grid gap-0.5 min-inline-0">
                    <span class="text-[0.8125rem]/tight font-semibold"
                      >{KIND_TEXT[kind].name()}</span
                    >
                    <span
                      class="line-clamp-2 text-xs/snug text-muted-foreground"
                      >{KIND_TEXT[kind].about()}</span
                    >
                  </span>
                </button>
              </li>
            {/each}
          </ul>
        </section>
      {:else}
        <p class="py-6 text-center text-sm text-muted-foreground">
          {m.board_catalog_empty()}
        </p>
      {/each}
    </div>
  </Dialog.Content>
</Dialog.Root>
