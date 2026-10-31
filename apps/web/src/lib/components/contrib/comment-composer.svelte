<script lang="ts">
  import { enhance } from "$app/forms";
  import { untrack } from "svelte";
  import type { Anchor } from "#lib/contrib.js";
  import Markdown from "#lib/components/markdown.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { commentLimit } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    action,
    anchor = null,
    parent = null,
    id = null,
    initial = "",
    placeholder = m.discussion_placeholder(),
    submit = m.discussion_comment(),
    autofocus = false,
    onclose,
  }: {
    action: string;
    anchor?: Anchor | null;
    parent?: string | null;
    id?: string | null;
    initial?: string;
    placeholder?: string;
    submit?: string;
    autofocus?: boolean;
    onclose?: () => void;
  } = $props();

  let body = $state(untrack(() => initial));
  let previewing = $state(false);
  let busy = $state(false);
  let message = $state<string | null>(null);
  let field = $state<HTMLTextAreaElement | null>(null);

  const left = $derived(commentLimit - body.length);

  $effect(() => {
    if (autofocus) {
      field?.focus();
    }
  });
</script>

<form
  method="POST"
  {action}
  class="grid gap-2"
  use:enhance={() => {
    busy = true;
    message = null;
    return async ({ result, update }) => {
      busy = false;
      if (result.type === "failure") {
        const failed = result.data?.message;
        message = typeof failed === "string" ? failed : null;
        return;
      }
      await update({ reset: false });
      if (result.type === "success") {
        body = "";
        previewing = false;
        onclose?.();
      }
    };
  }}
>
  {#if anchor != null}
    <input type="hidden" name="anchor" value={JSON.stringify(anchor)} />
  {/if}
  {#if parent != null}
    <input type="hidden" name="parent" value={parent} />
  {/if}
  {#if id != null}
    <input type="hidden" name="id" value={id} />
  {/if}
  <div class="flex items-baseline gap-4 border-be text-xs" role="tablist">
    {#each [false, true] as preview (preview)}
      <button
        type="button"
        role="tab"
        aria-selected={previewing === preview}
        class={cn(
          "-mbe-px border-be-2 border-transparent pbe-1 text-muted-foreground",
          {
            "border-foreground font-semibold text-foreground":
              previewing === preview,
          },
          { "hover:text-foreground": previewing !== preview },
        )}
        onclick={() => (previewing = preview)}
        >{preview ? m.discussion_preview() : m.discussion_write()}</button
      >
    {/each}
  </div>
  <Textarea
    bind:ref={field}
    bind:value={body}
    name="body"
    rows={parent == null && id == null ? 4 : 3}
    maxlength={commentLimit}
    aria-label={m.discussion_body_label()}
    {placeholder}
    class={cn({ "sr-only": previewing })}
    onkeydown={(event) => {
      if (
        event.key === "Enter" &&
        (event.metaKey || event.ctrlKey) &&
        body.trim() !== ""
      ) {
        event.currentTarget.form?.requestSubmit();
      }
      if (event.key === "Escape" && onclose != null) {
        onclose();
      }
    }}
  />
  {#if previewing}
    <div
      class="border-y border-dashed border-border py-2 text-sm min-block-24"
      role="tabpanel"
    >
      {#if body.trim() === ""}
        <p class="text-muted-foreground">{m.discussion_preview_empty()}</p>
      {:else}
        <Markdown {body} />
      {/if}
    </div>
  {/if}
  {#if message != null}
    <p class="text-sm text-destructive" role="alert">{message}</p>
  {/if}
  <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
    <p class="text-xs text-pretty text-muted-foreground">
      {#if left < 1000}
        <span class="font-figures"
          >{m.discussion_characters_left({ count: left })}</span
        >
      {:else}
        {m.discussion_hint()}
      {/if}
    </p>
    <div class="ms-auto flex gap-2">
      {#if onclose != null}
        <Button type="button" variant="ghost" size="sm" onclick={onclose}
          >{m.discussion_cancel()}</Button
        >
      {/if}
      <Button type="submit" size="sm" disabled={busy || body.trim() === ""}
        >{submit}</Button
      >
    </div>
  </div>
</form>
