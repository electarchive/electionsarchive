<script lang="ts">
  import CiteBody from "#lib/components/cite-body.svelte";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";

  let {
    published,
    release,
    isOpen = $bindable(false),
  }: {
    published: string | null;
    release: { calver: string; doi: string | null } | null;
    isOpen?: boolean;
  } = $props();

  const narrow = $derived(isNarrowLayout());
</script>

{#snippet body()}
  <div class="grid gap-3 p-3">
    <p class="text-[0.9375rem] font-semibold">{m.footer_cite()}</p>
    <CiteBody {published} {release} />
  </div>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open={isOpen}>
    <Drawer.Trigger
      class="ms-auto rounded-xs underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring max-sm:ms-0"
      >{m.footer_cite()}</Drawer.Trigger
    >
    <Drawer.Content>
      <Drawer.Header class="sr-only">
        <Drawer.Title>{m.footer_cite()}</Drawer.Title>
      </Drawer.Header>
      <div class="pbs-1 pbe-3">
        {@render body()}
      </div>
    </Drawer.Content>
  </Drawer.Root>
{:else}
  <Popover.Root bind:open={isOpen}>
    <Popover.Trigger
      class="ms-auto rounded-xs underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring max-sm:ms-0"
      >{m.footer_cite()}</Popover.Trigger
    >
    <Popover.Content
      align="end"
      side="top"
      class="gap-0 overflow-hidden p-0 inline-96"
    >
      {@render body()}
    </Popover.Content>
  </Popover.Root>
{/if}
