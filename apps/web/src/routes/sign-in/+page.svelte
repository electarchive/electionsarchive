<script lang="ts">
  import { enhance } from "$app/forms";
  import { resolve } from "$app/paths";
  import { authClient } from "#lib/auth-client.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let isPending = $state(false);
</script>

<svelte:head>
  <title>{m.sign_in_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <header class="grid gap-1.5">
    <h1
      class="font-serif text-[2rem] leading-[1.02] font-[640] tracking-[-0.01em]"
    >
      {m.sign_in_title()}
    </h1>
    <p class="text-[0.9375rem] text-muted-foreground">{m.sign_in_lede()}</p>
  </header>
  {#if data.providers.length > 0}
    <div class="grid gap-2">
      {#each data.providers as provider (provider)}
        <Button
          variant="outline"
          size="lg"
          onclick={async () =>
            authClient.signIn.social({ provider, callbackURL: data.next })}
        >
          {provider === "github" ? m.sign_in_github() : m.sign_in_google()}
        </Button>
      {/each}
    </div>
    <p
      class="flex items-center gap-3 text-xs text-muted-foreground before:flex-1 before:bg-border before:block-px after:flex-1 after:bg-border after:block-px"
    >
      {m.sign_in_or()}
    </p>
  {/if}
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
      />
    </div>
    <div class="grid gap-1.5">
      <Label for="password">{m.field_password()}</Label>
      <Input
        id="password"
        name="password"
        type="password"
        autocomplete="current-password"
        required
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
      >{m.sign_in_submit()}</Button
    >
  </form>
  <p class="text-sm text-muted-foreground">
    {m.sign_in_no_account()}
    <a
      class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
      href="{resolve('/sign-up')}?next={encodeURIComponent(data.next)}"
      >{m.sign_in_create()}</a
    >
  </p>
</main>
