<script lang="ts">
  import type { Snippet } from "svelte";
  import XIcon from "@lucide/svelte/icons/x";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import { m } from "#lib/paraglide/messages.js";

  let {
    isOpen = $bindable(false),
    title,
    href,
    action,
    children,
  }: {
    isOpen?: boolean;
    title: string;
    href: string | null;
    action: string;
    children: Snippet;
  } = $props();
</script>

<Drawer.Root bind:open={isOpen} shouldScaleBackground={false}>
  <Drawer.Content class="sm:mx-auto sm:max-inline-xl">
    <div
      class="relative grid gap-4 px-4 pbs-2 pbe-[calc(1.25rem+env(safe-area-inset-bottom))]"
    >
      <button
        type="button"
        class="absolute inset-e-2 inset-bs-0 z-10 grid place-items-center rounded-sm text-muted-foreground block-9 inline-9 hover:bg-secondary hover:text-foreground"
        aria-label={m.map_sheet_close()}
        onclick={() => (isOpen = false)}
      >
        <XIcon class="block-4 inline-4" />
      </button>
      <Drawer.Title class="pe-10 text-base/snug font-semibold text-pretty"
        >{title}</Drawer.Title
      >
      {@render children()}
      {#if href != null}
        <Button {href} size="lg" class="inline-full">{action}</Button>
      {/if}
    </div>
  </Drawer.Content>
</Drawer.Root>
