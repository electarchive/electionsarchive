<script lang="ts">
  import { enhance } from "$app/forms";
  import { resolve } from "$app/paths";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let isPending = $state(false);
</script>

<svelte:head>
  <title>{m.reset_page_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <h1 class="title">{m.reset_page_title()}</h1>
  {#if form?.done === true}
    <p class="text-[0.9375rem]" role="status">
      {m.reset_page_done()}
      <a
        class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve("/sign-in")}>{m.sign_in_title()}</a
      >
    </p>
  {:else if data.token == null || form?.invalid === true}
    <p class="text-[0.9375rem]" role="alert">
      {m.reset_page_invalid()}
      <a
        class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve("/forgot-password")}>{m.reset_page_request()}</a
      >
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
      <input type="hidden" name="token" value={data.token} />
      <div class="grid gap-1.5">
        <Label for="password">{m.field_new_password()}</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autocomplete="new-password"
          minlength={8}
          required
          aria-invalid={form?.message != null}
          aria-describedby={form?.message == null ? "hint" : "hint error"}
        />
        <p id="hint" class="text-xs text-muted-foreground">
          {m.field_password_hint()}
        </p>
      </div>
      {#if form?.message != null}
        <p id="error" class="text-sm text-destructive" role="alert">
          {form.message}
        </p>
      {/if}
      <Button type="submit" size="lg" disabled={isPending}
        >{m.reset_page_submit()}</Button
      >
    </form>
  {/if}
</main>
