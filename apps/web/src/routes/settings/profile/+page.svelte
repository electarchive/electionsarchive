<script lang="ts">
  import type { SubmitFunction } from "$app/forms";
  import { READ_ONLY } from "$app/env/public";
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import ContributorView from "#lib/components/contrib/contributor-view.svelte";
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let busy = $state<string | null>(null);
  let deleting = $state(false);
  let confirmation = $state("");

  const editing = $derived(page.url.searchParams.has("edit"));
  const problem = $derived(
    form != null && "message" in form
      ? {
          form: form.form,
          field: "field" in form ? form.field : "form",
          message: form.message,
        }
      : null,
  );
  const values = $derived.by(() => {
    const sent = form?.form === "profile" ? form.sent : null;
    return {
      handle: sent?.handle ?? data.handle ?? "",
      displayName: sent?.displayName ?? data.displayName,
      affiliation: sent?.affiliation ?? data.affiliation,
      bioMd: sent?.bioMd ?? data.bioMd,
      links: sent?.links ?? data.links,
    };
  });
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
        if (name === "profile" && result.type === "redirect") {
          await goto(result.location, {
            refreshAll: true,
            state: { saved: true },
          });
        } else {
          await update({
            reset: name !== "profile" && result.type === "success",
          });
        }
        if (result.type === "success" && name !== "profile") {
          confirmation = "";
        }
        busy = null;
      };
    };
</script>

<svelte:head>
  <title
    >{editing ? m.profile_edit() : m.profile_title()} | {m.wordmark()}</title
  >
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
  {#if !editing}
    {#if page.state.saved}
      <p class="mbe-5 border-s-2 border-foreground ps-3 text-sm" role="status">
        {m.profile_saved()}
      </p>
    {/if}
    {#if data.view == null}
      <header class="grid justify-items-start gap-3">
        <h1 class="title">{m.profile_title()}</h1>
        <p class="text-[0.9375rem] text-pretty text-muted-foreground">
          {m.profile_no_handle()}
        </p>
        <Button href="?edit" size="sm">{m.profile_edit()}</Button>
      </header>
    {:else}
      <ContributorView
        profile={data.view.profile}
        bio={data.view.bio}
        work={data.view.work}
      >
        {#snippet actions()}
          <div class="mbs-1.5 flex flex-wrap items-center gap-x-4 gap-y-2">
            <Button href="?edit" variant="outline" size="sm"
              >{m.profile_edit()}</Button
            >
            {#if !READ_ONLY}
              <a
                class="text-sm text-muted-foreground underline underline-offset-2"
                href="/contributors/{data.handle}">{m.profile_public_page()}</a
              >
            {/if}
          </div>
        {/snippet}
      </ContributorView>
    {/if}
  {:else}
    <div class="mx-auto grid max-inline-lg">
      <header class="grid gap-1.5">
        <h1 class="title">{m.profile_edit()}</h1>
        <p class="text-[0.9375rem] text-pretty text-muted-foreground">
          {m.profile_edit_lede()}
        </p>
      </header>

      <form
        method="POST"
        action="?edit&/profile"
        class="mbs-8 grid gap-5"
        use:enhance={submit("profile")}
      >
        <div class="grid gap-1.5">
          <Label for="handle">{m.username_field()}</Label>
          <Input
            id="handle"
            name="handle"
            required
            minlength={3}
            maxlength={30}
            pattern="[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]"
            autocomplete="username"
            autocapitalize="none"
            spellcheck={false}
            value={values.handle}
            aria-invalid={errorFor("profile", "handle") != null}
            aria-describedby={describedBy("profile", "handle", "handle-hint")}
          />
          <p id="handle-hint" class="text-xs text-pretty text-muted-foreground">
            {m.username_hint()}
          </p>
          {@render failure("profile", "handle")}
        </div>
        <div class="grid gap-1.5">
          <Label for="display_name">{m.profile_display_name()}</Label>
          <Input
            id="display_name"
            name="display_name"
            maxlength={60}
            autocomplete="name"
            value={values.displayName}
          />
        </div>
        <div class="grid gap-1.5">
          <Label for="affiliation">{m.profile_affiliation()}</Label>
          <Input
            id="affiliation"
            name="affiliation"
            maxlength={100}
            autocomplete="organization"
            value={values.affiliation}
            aria-describedby="affiliation-hint"
          />
          <p
            id="affiliation-hint"
            class="text-xs text-pretty text-muted-foreground"
          >
            {m.profile_affiliation_hint()}
          </p>
        </div>
        <div class="grid gap-1.5">
          <Label for="bio_md">{m.profile_bio()}</Label>
          <Textarea
            id="bio_md"
            name="bio_md"
            rows={4}
            maxlength={1000}
            value={values.bioMd}
            aria-invalid={errorFor("profile", "fields") != null}
            aria-describedby={describedBy("profile", "fields", "bio-hint")}
          />
          <p id="bio-hint" class="text-xs text-pretty text-muted-foreground">
            {m.profile_bio_hint()}
          </p>
          {@render failure("profile", "fields")}
        </div>
        <div class="grid gap-1.5">
          <Label for="links">{m.profile_links()}</Label>
          <Textarea
            id="links"
            name="links"
            rows={3}
            spellcheck={false}
            autocapitalize="none"
            value={values.links}
            placeholder="https://"
            aria-invalid={errorFor("profile", "links") != null}
            aria-describedby={describedBy("profile", "links", "links-hint")}
          />
          <p id="links-hint" class="text-xs text-pretty text-muted-foreground">
            {m.profile_links_hint()}
          </p>
          {@render failure("profile", "links")}
        </div>
        <div class="grid gap-1.5 border-y py-4">
          {#if data.licenseAcceptedAt == null}
            <div class="flex items-start gap-3">
              <Checkbox
                id="license"
                name="license"
                value="yes"
                class="mbs-0.5"
              />
              <Label for="license" class="grid gap-1 leading-snug font-normal">
                <span class="font-semibold">{m.profile_license()}</span>
                <span class="text-xs text-pretty text-muted-foreground"
                  >{m.profile_license_hint()}</span
                >
              </Label>
            </div>
          {:else}
            <p class="text-sm text-pretty">
              {m.profile_license_accepted({
                date: date(data.licenseAcceptedAt),
              })}
            </p>
          {/if}
        </div>
        {@render failure("profile", "form")}
        <div class="flex flex-wrap gap-3">
          <Button type="submit" size="lg" disabled={busy != null}
            >{m.profile_save()}</Button
          >
          <Button href="/settings/profile" variant="ghost" size="lg"
            >{m.profile_cancel()}</Button
          >
        </div>
      </form>

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
          action="?edit&/email"
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
          action="?edit&/password"
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
          action="?edit&/delete"
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
              >{m.profile_cancel()}</AlertDialog.Cancel
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
  {/if}
</main>
