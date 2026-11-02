<script lang="ts">
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let isPending = $state(false);
</script>

<svelte:head>
  <title>{m.verify_page_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <h1 class="title">{m.verify_page_title()}</h1>
  {#if data.verified}
    <p class="text-[0.9375rem]" role="status">{m.verify_page_done()}</p>
  {:else if form?.sent === true}
    <p class="text-[0.9375rem]" role="status">
      {m.verify_page_sent({ email: data.email })}
    </p>
  {:else}
    <p class="text-[0.9375rem] text-muted-foreground">
      {m.verify_page_lede({ email: data.email })}
    </p>
    <form
      method="POST"
      class="grid gap-4"
      use:enhance={() => {
        isPending = true;
        return async ({ update }) => {
          await update({ reset: false });
          isPending = false;
        };
      }}
    >
      {#if form?.wait === true}
        <p class="text-sm text-destructive" role="alert">
          {m.verify_page_wait()}
        </p>
      {/if}
      <Button type="submit" size="lg" disabled={isPending}
        >{m.verify_page_submit()}</Button
      >
    </form>
  {/if}
</main>
