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
  <title>{m.sign_up_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <header class="grid gap-1.5">
    <h1
      class="font-serif text-[2rem] leading-[1.02] font-[640] tracking-[-0.01em]"
    >
      {m.sign_up_title()}
    </h1>
    <p class="text-[0.9375rem] text-muted-foreground">{m.sign_up_lede()}</p>
  </header>
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
      <Label for="name">{m.field_name()}</Label>
      <Input
        id="name"
        name="name"
        autocomplete="name"
        required
        value={form?.name ?? ""}
      />
    </div>
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
        autocomplete="new-password"
        minlength={8}
        required
        aria-describedby="password-hint"
      />
      <p id="password-hint" class="text-xs text-muted-foreground">
        {m.field_password_hint()}
      </p>
    </div>
    {#if form?.message != null}
      <p class="text-sm text-destructive" role="alert">{form.message}</p>
    {/if}
    <Button type="submit" size="lg" disabled={isPending}
      >{m.sign_up_submit()}</Button
    >
  </form>
  <p class="text-sm text-muted-foreground">
    {m.sign_up_have_account()}
    <a
      class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
      href="{resolve('/sign-in')}?next={encodeURIComponent(data.next)}"
      >{m.sign_in_submit()}</a
    >
  </p>
</main>
