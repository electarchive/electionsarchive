<script lang="ts">
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import EyeIcon from "@lucide/svelte/icons/eye";
  import EyeOffIcon from "@lucide/svelte/icons/eye-off";
  import { m } from "#lib/paraglide/messages.js";

  const {
    kind,
    id,
    watching,
  }: {
    kind: "entity" | "polity" | "proposal" | "user";
    id: string;
    watching: boolean;
  } = $props();

  let pending = $state(false);
</script>

<form
  method="POST"
  action="/watch"
  use:enhance={() => {
    pending = true;
    return async ({ update }) => {
      await update({ reset: false });
      pending = false;
    };
  }}
>
  <input type="hidden" name="kind" value={kind} />
  <input type="hidden" name="id" value={id} />
  <input type="hidden" name="on" value={String(!watching)} />
  <input
    type="hidden"
    name="back"
    value={page.url.pathname + page.url.search}
  />
  <button
    type="submit"
    class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary disabled:opacity-60"
    disabled={pending}
  >
    {#if watching}
      <EyeOffIcon class="block-3.5 inline-3.5" aria-hidden="true" />
      {m.watch_stop()}
    {:else}
      <EyeIcon class="block-3.5 inline-3.5" aria-hidden="true" />
      {m.watch_start()}
    {/if}
  </button>
</form>
