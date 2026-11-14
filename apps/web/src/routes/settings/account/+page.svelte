<script lang="ts">
  import type { SubmitFunction } from "$app/forms";
  import { enhance } from "$app/forms";
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let busy = $state<string | null>(null);
  let deleting = $state(false);
  let confirmation = $state("");

  const problem = $derived(
    form != null && "message" in form
      ? {
          form: form.form,
          field: "field" in form ? form.field : "form",
          message: form.message,
        }
      : null,
  );
  const passwordDone = $derived(
    form?.form === "password" && "done" in form ? form.done : null,
  );
  const ready = $derived(
    data.hasPassword
      ? confirmation !== ""
      : confirmation.trim().toLowerCase() === data.email.toLowerCase(),
  );

  const errorFor = (name: string, field: string) =>
    problem?.form === name && problem.field === field ? problem.message : null;
  const describedBy = (name: string, field: string, hint: string | null) =>
    errorFor(name, field) == null ? hint : `${name}-${field}-error`;

  const submit =
    (name: string): SubmitFunction =>
    () => {
      busy = name;
      return async ({ result, update }) => {
        await update({ reset: result.type === "success" });
        if (result.type === "success") {
          confirmation = "";
        }
        busy = null;
      };
    };
</script>

<svelte:head>
  <title>{m.settings_account_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet failure(name: string, field: string)}
  {#if errorFor(name, field) != null}
    <p
      id="{name}-{field}-error"
      class="text-sm text-pretty text-destructive"
      role="alert"
    >
      {errorFor(name, field)}
    </p>
  {/if}
{/snippet}

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <div class="mx-auto grid max-inline-lg">
    <header class="grid gap-1.5">
      <h1 class="title">{m.settings_account_title()}</h1>
      <p class="text-[0.9375rem] text-pretty text-muted-foreground">
        {m.settings_account_lede()}
      </p>
    </header>

      <section
        class="mbs-12 grid gap-4 border-bs pbs-6"
        aria-labelledby="email-heading"
      >
        <div class="grid gap-1">
          <h2 id="email-heading" class="heading">{m.email_heading()}</h2>
          <p class="text-sm text-pretty">
            {m.email_current({ email: data.email })}
            <span class="text-muted-foreground"
              >{data.emailVerified
                ? m.email_verified()
                : m.email_unverified()}</span
            >
            {#if !data.emailVerified}
              <a class="underline underline-offset-2" href="/verify-email"
                >{m.email_verify_link()}</a
              >
            {/if}
          </p>
        </div>
        {#if data.pendingEmail != null}
          <p
            class="border-s-2 border-foreground ps-3 text-sm text-pretty"
            role="status"
          >
            {data.emailVerified
              ? m.email_pending({
                  email: data.email,
                  newEmail: data.pendingEmail,
                })
              : m.email_pending_unverified({ newEmail: data.pendingEmail })}
          </p>
        {/if}
        <form
          method="POST"
          action="?/email"
          class="grid gap-4"
          use:enhance={submit("email")}
        >
          <div class="grid gap-1.5">
            <Label for="new-email">{m.email_new()}</Label>
            <Input
              id="new-email"
              name="email"
              type="email"
              required
              maxlength={254}
              autocomplete="email"
              spellcheck={false}
              value={form?.form === "email" && "sent" in form ? form.sent : ""}
              aria-invalid={errorFor("email", "form") != null}
              aria-describedby={describedBy("email", "form", "email-hint")}
            />
            <p
              id="email-hint"
              class="text-xs text-pretty text-muted-foreground"
            >
              {data.emailVerified
                ? m.email_change_hint({ email: data.email })
                : m.email_change_hint_unverified()}
            </p>
            {@render failure("email", "form")}
          </div>
          <div>
            <Button type="submit" variant="outline" disabled={busy != null}
              >{m.email_change()}</Button
            >
          </div>
        </form>
      </section>

      <section
        class="mbs-12 grid gap-4 border-bs pbs-6"
        aria-labelledby="password-heading"
      >
        <div class="grid gap-1">
          <h2 id="password-heading" class="heading">{m.password_heading()}</h2>
          {#if !data.hasPassword}
            <p class="text-sm text-pretty text-muted-foreground">
              {m.password_set_lede()}
            </p>
          {/if}
        </div>
        <form
          method="POST"
          action="?/password"
          class="grid gap-4"
          use:enhance={submit("password")}
        >
          <input
            type="email"
            name="username"
            autocomplete="username"
            value={data.email}
            hidden
            readonly
          />
          {#if data.hasPassword}
            <div class="grid gap-1.5">
              <Label for="current-password">{m.password_current()}</Label>
              <Input
                id="current-password"
                name="current"
                type="password"
                required
                maxlength={128}
                autocomplete="current-password"
                aria-invalid={errorFor("password", "current") != null}
                aria-describedby={describedBy("password", "current", null)}
              />
              {@render failure("password", "current")}
            </div>
          {/if}
          <div class="grid gap-1.5">
            <Label for="new-password">{m.password_new()}</Label>
            <Input
              id="new-password"
              name="password"
              type="password"
              required
              minlength={8}
              maxlength={128}
              autocomplete="new-password"
              aria-invalid={errorFor("password", "password") != null}
              aria-describedby={describedBy(
                "password",
                "password",
                "password-hint",
              )}
            />
            <p
              id="password-hint"
              class="text-xs text-pretty text-muted-foreground"
            >
              {m.password_rules()}
            </p>
            {@render failure("password", "password")}
          </div>
          <div class="grid gap-1.5">
            <Label for="confirm-password">{m.password_confirm()}</Label>
            <Input
              id="confirm-password"
              name="confirm"
              type="password"
              required
              minlength={8}
              maxlength={128}
              autocomplete="new-password"
              aria-invalid={errorFor("password", "confirm") != null}
              aria-describedby={describedBy("password", "confirm", null)}
            />
            {@render failure("password", "confirm")}
          </div>
          <div class="flex items-start gap-3">
            <Checkbox
              id="revoke"
              name="revoke"
              value="yes"
              checked
              class="mbs-0.5"
            />
            <Label for="revoke" class="grid gap-1 leading-snug font-normal">
              <span class="font-semibold">{m.password_revoke()}</span>
              <span class="text-xs text-pretty text-muted-foreground"
                >{m.password_revoke_hint()}</span
              >
            </Label>
          </div>
          {@render failure("password", "form")}
          {#if passwordDone != null}
            <p class="text-sm" role="status">{passwordDone}</p>
          {/if}
          <div>
            <Button type="submit" variant="outline" disabled={busy != null}
              >{data.hasPassword
                ? m.password_change()
                : m.password_set()}</Button
            >
          </div>
        </form>
      </section>

      <section
        class="mbs-24 grid justify-items-start gap-3 border-bs-2 border-destructive/50 pbs-6"
        aria-labelledby="delete-heading"
      >
        <h2 id="delete-heading" class="heading">
          {m.delete_account_heading()}
        </h2>
        {#if data.staff}
          <p class="text-sm text-pretty text-muted-foreground">
            {m.delete_account_staff()}
          </p>
        {:else}
          <p class="text-sm text-pretty text-muted-foreground">
            {m.delete_account_lede()}
          </p>
          <Button
            variant="destructive"
            onclick={() => {
              confirmation = "";
              deleting = true;
            }}>{m.delete_account_open()}</Button
          >
        {/if}
      </section>
    </div>

    <AlertDialog.Root bind:open={deleting}>
      <AlertDialog.Content>
        <form
          method="POST"
          action="?/delete"
          class="grid gap-5"
          use:enhance={submit("delete")}
        >
          <AlertDialog.Header>
            <AlertDialog.Title>{m.delete_account_title()}</AlertDialog.Title>
            <AlertDialog.Description
              >{m.delete_account_lede()}</AlertDialog.Description
            >
          </AlertDialog.Header>
          <div class="grid gap-1.5">
            {#if data.hasPassword}
              <input
                type="email"
                name="username"
                autocomplete="username"
                value={data.email}
                hidden
                readonly
              />
              <Label for="delete-confirm">{m.delete_account_password()}</Label>
              <Input
                id="delete-confirm"
                name="confirm"
                type="password"
                required
                maxlength={128}
                autocomplete="current-password"
                bind:value={confirmation}
                aria-invalid={errorFor("delete", "form") != null}
                aria-describedby={describedBy("delete", "form", null)}
              />
            {:else}
              <Label for="delete-confirm"
                >{m.delete_account_type({ email: data.email })}</Label
              >
              <Input
                id="delete-confirm"
                name="confirm"
                type="text"
                required
                autocomplete="off"
                autocapitalize="none"
                spellcheck={false}
                bind:value={confirmation}
                aria-invalid={errorFor("delete", "form") != null}
                aria-describedby={describedBy("delete", "form", null)}
              />
            {/if}
            {@render failure("delete", "form")}
          </div>
          <AlertDialog.Footer>
            <AlertDialog.Cancel type="button" disabled={busy === "delete"}
              >{m.settings_cancel()}</AlertDialog.Cancel
            >
            <Button
              type="submit"
              variant="destructive"
              disabled={!ready || busy != null}
              >{m.delete_account_confirm()}</Button
            >
          </AlertDialog.Footer>
        </form>
      </AlertDialog.Content>
    </AlertDialog.Root>
</main>
