<script lang="ts">
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const copy = {
    direct: {
      heading: m.unsubscribe_heading_direct,
      body: m.unsubscribe_body_direct,
      done: m.unsubscribe_done_direct,
    },
    digest: {
      heading: m.unsubscribe_heading_digest,
      body: m.unsubscribe_body_digest,
      done: m.unsubscribe_done_digest,
    },
  };

  const done = $derived(form != null && "done" in form ? form.done : null);
  const invalid = $derived(
    data.list == null ||
      data.subscribed == null ||
      (form != null && "invalid" in form),
  );
</script>

<svelte:head>
  <title>{m.unsubscribe_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-5 px-4 pbs-12 pbe-20 max-inline-lg">
  {#if done != null}
    <h1 class="title">{m.unsubscribe_title()}</h1>
    <p class="text-[0.9375rem] text-pretty" role="status">
      {copy[done].done()}
    </p>
  {:else if invalid || data.list == null}
    <h1 class="title">{m.unsubscribe_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {m.unsubscribe_invalid()}
    </p>
  {:else if data.subscribed === false}
    <h1 class="title">{m.unsubscribe_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {m.unsubscribe_already()}
    </p>
  {:else}
    <h1 class="title">{copy[data.list].heading()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {copy[data.list].body()}
    </p>
    <form method="POST" use:enhance>
      <Button type="submit" size="lg">{m.unsubscribe_confirm()}</Button>
    </form>
  {/if}
  <p class="text-sm">
    <a class="underline underline-offset-2" href="/settings/notifications"
      >{m.unsubscribe_settings()}</a
    >
  </p>
</main>
