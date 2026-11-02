<script lang="ts">
  import { enhance } from "$app/forms";
  import { untrack } from "svelte";
  import type { Anchor } from "#lib/contrib.js";
  import type { parseMarkdown } from "#lib/markdown-tree.js";
  import Markdown from "#lib/components/markdown.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Tabs } from "#lib/components/ui/tabs/index.js";
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
    suggestable = null,
    titled = false,
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
    suggestable?: Record<string, string> | null;
    titled?: boolean;
    onclose?: () => void;
  } = $props();

  const labels: Record<string, () => string> = {
    text: m.discussion_field_text,
    body_md: m.discussion_field_text,
    value: m.discussion_field_value,
    color: m.proposal_field_color,
  };

  const tabsId = $props.id();

  let body = $state(untrack(() => initial));
  let title = $state("");
  let previewing = $state(false);
  let parse = $state<typeof parseMarkdown | null>(null);
  let busy = $state(false);
  let message = $state<string | null>(null);
  let field = $state<HTMLTextAreaElement | null>(null);
  let suggesting = $state(false);
  let suggested = $state<Record<string, string>>(
    untrack(() => ({ ...suggestable })),
  );

  const left = $derived(commentLimit - body.length);

  $effect(() => {
    if (previewing && parse == null) {
      void import("#lib/markdown-tree.js").then((module) => {
        parse = module.parseMarkdown;
      });
    }
  });
  const changed = $derived.by(() => {
    if (!suggesting || suggestable == null) {
      return null;
    }
    const values: Record<string, string> = {};
    for (const [name, value] of Object.entries(suggested)) {
      if (value !== suggestable[name]) {
        values[name] = value;
      }
    }
    return Object.keys(values).length > 0 ? values : null;
  });

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
        title = "";
        previewing = false;
        suggesting = false;
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
  {#if changed != null}
    <input
      type="hidden"
      name="suggestion"
      value={JSON.stringify({ values: changed })}
    />
  {/if}
  {#if id != null}
    <input type="hidden" name="id" value={id} />
  {/if}
  {#if titled}
    <label class="grid gap-1 text-sm font-semibold">
      {m.talk_new_title()}
      <Input
        bind:value={title}
        name="title"
        required
        maxlength={200}
        placeholder={m.talk_new_title_placeholder()}
        class="font-normal"
      />
    </label>
  {/if}
  <Tabs
    id={tabsId}
    panel="{tabsId}-panel"
    label={m.discussion_body_label()}
    size="sm"
    options={[
      { value: "write", label: m.discussion_write() },
      { value: "preview", label: m.discussion_preview() },
    ]}
    value={previewing ? "preview" : "write"}
    onValueChange={(next: string) => (previewing = next === "preview")}
  />
  <div
    id="{tabsId}-panel"
    role="tabpanel"
    aria-labelledby="{tabsId}-{previewing ? 'preview' : 'write'}"
    class="grid"
  >
    <Textarea
      bind:ref={field}
      bind:value={body}
      name="body"
      rows={parent == null && id == null ? 4 : 3}
      maxlength={commentLimit}
      aria-label={m.discussion_body_label()}
      {placeholder}
      class={cn({ "sr-only": previewing })}
      onkeydown={(
        event: KeyboardEvent & { currentTarget: HTMLTextAreaElement },
      ) => {
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
      >
        {#if body.trim() === ""}
          <p class="text-muted-foreground">{m.discussion_preview_empty()}</p>
        {:else if parse != null}
          <Markdown tree={parse(body)} />
        {/if}
      </div>
    {/if}
  </div>
  {#if suggesting && suggestable != null}
    <fieldset class="grid gap-2 border-s-2 border-marker ps-3">
      <legend class="text-xs text-pretty text-muted-foreground"
        >{m.discussion_suggest_hint()}</legend
      >
      {#each Object.keys(suggestable) as name (name)}
        <label class="grid gap-1 text-xs text-muted-foreground">
          {labels[name]?.() ?? name}
          {#if name === "body_md"}
            <Textarea
              bind:value={suggested[name]}
              rows={3}
              maxlength={commentLimit}
              class="text-foreground"
            />
          {:else}
            <Input
              bind:value={suggested[name]}
              maxlength={name === "color" ? 7 : 500}
              class="text-foreground"
            />
          {/if}
        </label>
      {/each}
    </fieldset>
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
      {#if suggestable != null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-pressed={suggesting}
          onclick={() => {
            suggesting = !suggesting;
            suggested = { ...suggestable };
          }}
          >{suggesting
            ? m.discussion_suggest_remove()
            : m.discussion_suggest()}</Button
        >
      {/if}
      {#if onclose != null}
        <Button type="button" variant="ghost" size="sm" onclick={onclose}
          >{m.discussion_cancel()}</Button
        >
      {/if}
      <Button
        type="submit"
        size="sm"
        disabled={busy || body.trim() === "" || (titled && title.trim() === "")}
        >{submit}</Button
      >
    </div>
  </div>
</form>
