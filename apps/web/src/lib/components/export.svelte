<script lang="ts">
  import { page } from "$app/state";
  import ShareIcon from "@lucide/svelte/icons/share";
  import { getContext, onDestroy, untrack } from "svelte";
  import type ExportMenu from "#lib/components/export-menu.svelte";
  import { idle } from "#lib/idle.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { name, class: className }: { name: string; class?: string } = $props();

  const claimed = getContext<Set<string>>("figures");
  const figure = untrack(() => {
    let slot = 0;
    while (claimed.has(`${name}:${slot}`)) {
      slot += 1;
    }
    const key = `${name}:${slot}`;
    claimed.add(key);
    onDestroy(() => claimed.delete(key));
    return slot === 0 ? name : `${name}-${slot + 1}`;
  });

  let Menu = $state<typeof ExportMenu | null>(null);
  let isOpen = $state(false);

  function load() {
    void import("#lib/components/export-menu.svelte")
      .then((module) => {
        Menu = module.default;
      })
      .catch(() => {});
  }

  $effect(() => idle(load));
</script>

<div
  class={cn("flex", className)}
  data-export-skip
  data-figure={figure}
  data-embedded={page.url.searchParams.get("embed") === figure ? "" : undefined}
>
  {#if Menu == null}
    <button
      type="button"
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground"
      aria-label={m.export_menu()}
      title={m.export_menu()}
      onclick={() => {
        isOpen = true;
        load();
      }}
    >
      <ShareIcon class="block-4 inline-4" />
    </button>
  {:else}
    <Menu {name} {figure} bind:isOpen />
  {/if}
</div>
