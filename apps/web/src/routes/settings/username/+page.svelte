<script lang="ts">
  import { enhance } from "$app/forms";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Label } from "#lib/components/ui/label/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  let isPending = $state(false);

  const problem = $derived(
    form != null && "message" in form ? form.message : null,
  );
  const typed = $derived(
    form != null && "handle" in form ? form.handle : data.handle,
  );
</script>

<svelte:head>
  <title>{m.username_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto grid gap-6 px-4 pbs-12 pbe-20 max-inline-sm">
  <header class="grid gap-1.5">
    <h1 class="title">{m.username_title()}</h1>
    <p class="text-[0.9375rem] text-pretty text-muted-foreground">
      {data.handle == null
        ? m.username_lede()
        : m.username_current({ handle: data.handle })}
    </p>
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
      <Label for="handle">{m.username_field()}</Label>
      <Input
        id="handle"
        name="handle"
        required
        minlength={3}
        maxlength={30}
        pattern="[a-z0-9\-]+"
        autocomplete="username"
        autocapitalize="none"
        spellcheck={false}
        value={typed ?? ""}
        aria-invalid={problem != null}
        aria-describedby={problem == null ? "handle-hint" : "handle-error"}
      />
      <p id="handle-hint" class="text-xs text-muted-foreground">
        {m.username_hint()}
      </p>
    </div>
    {#if problem != null}
      <p id="handle-error" class="text-sm text-destructive" role="alert">
        {problem}
      </p>
    {:else if form != null && "saved" in form}
      <p class="text-sm" role="status">{m.username_saved()}</p>
    {/if}
    <Button type="submit" size="lg" disabled={isPending}
      >{data.handle == null ? m.username_claim() : m.username_save()}</Button
    >
  </form>
</main>
