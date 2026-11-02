<script lang="ts">
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Checkbox } from "#lib/components/ui/checkbox/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const cadences = [
    { value: "off", label: m.notify_settings_digest_off() },
    { value: "daily", label: m.notify_settings_digest_daily() },
    { value: "weekly", label: m.notify_settings_digest_weekly() },
  ] as const;

  let isPending = $state(false);
</script>

<svelte:head>
  <title>{m.notify_settings_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-lg">
  <header class="grid gap-1.5">
    <h1 class="title">{m.notify_settings_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {m.notify_settings_lede()}
    </p>
  </header>
  {#if !data.verified}
    <p class="border-y py-3 text-sm text-pretty" role="note">
      {m.notify_settings_unverified()}
      <a class="underline underline-offset-2" href="/verify-email"
        >{m.notify_settings_verify()}</a
      >
    </p>
  {/if}
  <form
    method="POST"
    class="grid gap-6"
    use:enhance={() => {
      isPending = true;
      return async ({ update }) => {
        await update({ reset: false });
        isPending = false;
      };
    }}
  >
    <div class="flex items-start gap-3">
      <Checkbox
        id="direct"
        name="direct"
        value="yes"
        checked={data.direct}
        class="mbs-0.5"
        aria-describedby="direct-hint"
      />
      <div class="grid gap-1">
        <Label for="direct" class="leading-snug"
          >{m.notify_settings_direct()}</Label
        >
        <p id="direct-hint" class="text-xs text-pretty text-muted-foreground">
          {m.notify_settings_direct_hint()}
        </p>
      </div>
    </div>
    <fieldset class="grid gap-2 border-bs pbs-5" aria-describedby="digest-hint">
      <legend class="float-start mbe-1 text-sm font-semibold"
        >{m.notify_settings_digest()}</legend
      >
      <p
        id="digest-hint"
        class="clear-start text-xs text-pretty text-muted-foreground"
      >
        {m.notify_settings_digest_hint()}
      </p>
      <div class="flex flex-wrap gap-2 pbs-1">
        {#each cadences as cadence (cadence.value)}
          <label
            class="flex cursor-pointer items-center gap-2 rounded-sm border border-input px-3 text-sm block-9 has-checked:border-foreground has-checked:font-semibold has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring"
          >
            <input
              type="radio"
              name="digest"
              value={cadence.value}
              checked={data.digest === cadence.value}
              class="accent-foreground"
            />
            {cadence.label}
          </label>
        {/each}
      </div>
    </fieldset>
    {#if form != null && "message" in form}
      <p class="text-sm text-destructive" role="alert">{form.message}</p>
    {/if}
    <p class="text-sm" role="status">
      {#if form != null && "saved" in form}{m.notify_settings_saved()}{/if}
    </p>
    <Button type="submit" size="lg" disabled={isPending}
      >{m.notify_settings_save()}</Button
    >
  </form>
</main>
