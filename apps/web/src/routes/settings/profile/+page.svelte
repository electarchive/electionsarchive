<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let isPending = $state(false);

  const problem = $derived(
    form != null && "message" in form
      ? { field: form.field, message: form.message }
      : null,
  );
  const values = $derived.by(() => {
    const sent = form != null && "sent" in form ? form.sent : undefined;
    return {
      handle: sent?.handle ?? data.handle,
      displayName: sent?.displayName ?? data.displayName,
      affiliation: sent?.affiliation ?? data.affiliation,
      bioMd: sent?.bioMd ?? data.bioMd,
      links: sent?.links ?? data.links,
    };
  });
  const describedBy = (field: string, hint: string | null) =>
    problem?.field === field ? `${field}-error` : hint;
</script>

<svelte:head>
  <title>{m.profile_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet failure(field: string)}
  {#if problem?.field === field}
    <p id="{field}-error" class="text-sm text-destructive" role="alert">
      {problem.message}
    </p>
  {/if}
{/snippet}

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-lg">
  <header class="grid gap-1.5">
    <h1 class="title">{m.profile_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {#if data.handle == null || READ_ONLY}
        {m.profile_lede()}
      {:else}
        {m.profile_lede_public()}
        <a
          class="text-foreground underline underline-offset-2"
          href="/contributors/{data.handle}">/contributors/{data.handle}</a
        >
      {/if}
    </p>
  </header>
  <form
    method="POST"
    class="grid gap-5"
    use:enhance={() => {
      isPending = true;
      return async ({ update }) => {
        await update({ reset: false });
        isPending = false;
      };
    }}
  >
    <div class="grid gap-1.5">
      <Label for="handle">{m.username_field()}</Label>
      <Input
        id="handle"
        name="handle"
        required
        minlength={3}
        maxlength={30}
        pattern="[a-z0-9][a-z0-9\-]*[a-z0-9]"
        autocomplete="username"
        autocapitalize="none"
        spellcheck={false}
        value={values.handle ?? ""}
        aria-invalid={problem?.field === "handle"}
        aria-describedby={describedBy("handle", "handle-hint")}
      />
      <p id="handle-hint" class="text-xs text-pretty text-muted-foreground">
        {m.username_hint()}
      </p>
      {@render failure("handle")}
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
        aria-describedby="bio-hint"
      />
      <p id="bio-hint" class="text-xs text-pretty text-muted-foreground">
        {m.profile_bio_hint()}
      </p>
      {@render failure("fields")}
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
        aria-invalid={problem?.field === "links"}
        aria-describedby={describedBy("links", "links-hint")}
      />
      <p id="links-hint" class="text-xs text-pretty text-muted-foreground">
        {m.profile_links_hint()}
      </p>
      {@render failure("links")}
    </div>
    <div class="grid gap-1.5 border-y py-4">
      {#if data.licenseAcceptedAt == null}
        <div class="flex items-start gap-3">
          <Checkbox id="license" name="license" value="yes" class="mbs-0.5" />
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
            date: date(data.licenseAcceptedAt.slice(0, 10)),
          })}
        </p>
      {/if}
    </div>
    {@render failure("form")}
    {#if form != null && "saved" in form}
      <p class="text-sm" role="status">{m.username_saved()}</p>
    {/if}
    <Button type="submit" size="lg" disabled={isPending}
      >{data.handle == null ? m.username_claim() : m.profile_save()}</Button
    >
  </form>
</main>
