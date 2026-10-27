<script lang="ts">
  import XIcon from "@lucide/svelte/icons/x";
  import PartyCard from "#lib/components/party-card.svelte";
  import PersonCard from "#lib/components/person-card.svelte";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import { explain } from "#lib/explain.svelte.js";
  import { m } from "#lib/paraglide/messages.js";

  const value = $derived(explain.value);
</script>

<Drawer.Root bind:open={explain.isOpen} shouldScaleBackground={false}>
  <Drawer.Content
    class="sm:mx-auto sm:max-inline-xl"
    onCloseAutoFocus={(event: Event) => {
      event.preventDefault();
      explain.anchor?.focus({ preventScroll: true });
    }}
  >
    <div class="relative pbe-[env(safe-area-inset-bottom)]">
      <button
        type="button"
        class="absolute inset-e-2 inset-bs-0.5 z-10 grid place-items-center rounded-sm text-muted-foreground block-9 inline-9 hover:bg-secondary hover:text-foreground"
        aria-label={m.map_sheet_close()}
        onclick={() => (explain.isOpen = false)}
      >
        <XIcon class="block-4 inline-4" />
      </button>
      {#if value?.kind === "term"}
        <div class="grid gap-1.5 px-4 pbs-2 pbe-6">
          <Drawer.Title class="pe-10 text-base/snug font-semibold text-pretty"
            >{value.label}</Drawer.Title
          >
          <Drawer.Description
            class="text-[0.9375rem]/relaxed text-pretty text-foreground max-inline-[62ch]"
            >{value.definition}</Drawer.Description
          >
        </div>
      {:else if value != null}
        <Drawer.Title class="sr-only">{m.explain_preview()}</Drawer.Title>
        <div class="pbs-5 pbe-2">
          {#if value.kind === "person"}
            <PersonCard id={value.id} />
          {:else}
            <PartyCard
              id={value.id}
              color={value.color}
              within={value.within}
            />
          {/if}
        </div>
      {/if}
    </div>
  </Drawer.Content>
</Drawer.Root>
