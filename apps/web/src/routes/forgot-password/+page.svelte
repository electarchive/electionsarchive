<script lang="ts">
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData } from "./$types";

  const { form }: { form: ActionData } = $props();

  let isPending = $state(false);
</script>

<svelte:head>
  <title>{m.forgot_page_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <header class="grid gap-1.5">
    <h1 class="title">{m.forgot_page_title()}</h1>
    <p class="text-[0.9375rem] text-muted-foreground">
      {m.forgot_page_lede()}
    </p>
  </header>
  {#if form?.sent === true}
    <p class="text-[0.9375rem]" role="status">
      {m.forgot_page_sent({ email: form.email })}
    </p>
  {:else}
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
      <div class="grid gap-1.5">
        <Label for="email">{m.field_email()}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autocomplete="email"
          required
          value={form?.email ?? ""}
          aria-invalid={form?.message != null}
          aria-describedby={form?.message == null ? undefined : "error"}
        />
      </div>
      {#if form?.message != null}
        <p id="error" class="text-sm text-destructive" role="alert">
          {form.message}
        </p>
      {/if}
      <Button type="submit" size="lg" disabled={isPending}
        >{m.forgot_page_submit()}</Button
      >
    </form>
  {/if}
</main>
