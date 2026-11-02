<script lang="ts">
  import type CitePanel from "#lib/components/cite-panel.svelte";
  import { idle } from "#lib/idle.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    published,
    release,
  }: {
    published: string | null;
    release: { calver: string; doi: string | null } | null;
  } = $props();

  let Panel = $state<typeof CitePanel | null>(null);
  let isOpen = $state(false);

  function load() {
    void import("#lib/components/cite-panel.svelte")
      .then((module) => {
        Panel = module.default;
      })
      .catch(() => {});
  }

  $effect(() => idle(load));
</script>

{#if Panel == null}
  <button
    type="button"
    class="ms-auto rounded-xs underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring max-sm:ms-0"
    onclick={() => {
      isOpen = true;
      load();
    }}>{m.footer_cite()}</button
  >
{:else}
  <Panel {published} {release} bind:isOpen />
{/if}
