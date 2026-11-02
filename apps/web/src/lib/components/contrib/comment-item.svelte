<script lang="ts">
  import { enhance } from "$app/forms";
  import type { CommentView } from "#lib/contrib.js";
  import CommentComposer from "#lib/components/contrib/comment-composer.svelte";
  import CommentItem from "#lib/components/contrib/comment-item.svelte";
  import Proof from "#lib/components/contrib/proof.svelte";
  import Markdown from "#lib/components/markdown.svelte";
  import Moment from "#lib/components/moment.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { anchorId } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    comment,
    replies = [],
    head,
    canReply,
    canModerate,
    canAccept = false,
    showAnchor = true,
  }: {
    comment: CommentView;
    replies?: Array<CommentView>;
    head: number | null;
    canReply: boolean;
    canModerate: boolean;
    canAccept?: boolean;
    showAnchor?: boolean;
  } = $props();

  const labels: Record<string, () => string> = {
    text: m.discussion_field_text,
    body_md: m.discussion_field_text,
    value: m.discussion_field_value,
    color: m.proposal_field_color,
  };

  let mode = $state<"read" | "edit" | "reply" | "hide">("read");
  let reason = $state("");

  const author = $derived(comment.author ?? m.proposal_unknown_author());
  const hidden = $derived(comment.hidden_reason != null);
  const outdated = $derived(
    comment.revision_n != null && head != null && comment.revision_n !== head,
  );
  let accepting = $state(false);
</script>

<article class="grid gap-1.5" id="comment-{comment.id}">
  <header class="flex flex-wrap items-baseline gap-x-2 text-sm">
    {#if comment.handle == null}
      <span class="font-semibold">{author}</span>
    {:else}
      <a
        class="font-semibold underline-offset-2 hover:underline"
        href="/contributors/{comment.handle}">{author}</a
      >
    {/if}
    {#if comment.agent_name != null}
      <span class="text-xs text-muted-foreground"
        >{m.discussion_agent({
          agent: comment.agent_name,
          model: comment.agent_model ?? "",
        })}</span
      >
    {/if}
    <a
      class="ms-auto font-figures text-xs text-muted-foreground underline-offset-2 hover:underline"
      href="#comment-{comment.id}"
      ><Moment
        at={comment.created_at}
        style="date"
      />{#if comment.edited_at != null}
        <span class="ms-1">({m.discussion_edited()})</span>{/if}</a
    >
  </header>
  {#if (showAnchor && comment.anchor != null) || outdated}
    <p class="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
      {#if showAnchor && comment.anchor != null}
        {#if outdated}
          <span class="border-s-2 border-border ps-1.5"
            >{m.discussion_on({ label: comment.anchor.label })}</span
          >
        {:else}
          <a
            class="border-s-2 border-foreground ps-1.5 text-foreground underline-offset-2 hover:underline"
            href="#{anchorId(comment.anchor)}"
            >{m.discussion_on({ label: comment.anchor.label })}</a
          >
        {/if}
      {/if}
      {#if outdated && comment.revision_n != null}
        <span class="font-figures"
          >{m.discussion_on_revision({ n: comment.revision_n })}</span
        >
      {/if}
    </p>
  {/if}
  {#if hidden}
    <p class="text-sm text-pretty text-muted-foreground">
      {m.discussion_hidden({
        name: comment.hidden_by ?? m.proposal_unknown_author(),
        reason: comment.hidden_reason ?? "",
      })}
    </p>
  {/if}
  {#if mode === "edit" && comment.body != null}
    <CommentComposer
      action="?/edit_comment"
      id={comment.id}
      initial={comment.body}
      submit={m.discussion_save()}
      autofocus
      onclose={() => (mode = "read")}
    />
  {:else if comment.body != null}
    <div class={cn("text-sm", { "text-muted-foreground": hidden })}>
      <Markdown body={comment.body} />
    </div>
  {/if}
  {#if comment.suggestion != null && !hidden}
    {const suggestion = $derived(comment.suggestion)}
    <div
      class="grid gap-1.5 border-s-2 border-marker py-1 ps-3 text-sm"
      aria-label={m.discussion_suggested()}
      role="group"
    >
      <p class="text-xs text-muted-foreground">{m.discussion_suggested()}</p>
      {#each Object.entries(suggestion.values) as [name, value] (name)}
        <div class="grid gap-0.5">
          <span class="text-xs text-muted-foreground"
            >{labels[name]?.() ?? name}</span
          >
          <Proof
            class={cn("flex-wrap", {
              "whitespace-pre-line": name === "body_md",
            })}
            before={suggestion.before[name] ?? null}
            after={value}
          />
        </div>
      {/each}
      {#if comment.accepted_revision != null}
        <p class="text-xs font-semibold">
          {m.discussion_accepted({ n: comment.accepted_revision })}
        </p>
      {:else if outdated}
        <p class="text-xs text-muted-foreground">
          {m.discussion_suggestion_outdated()}
        </p>
      {:else if canAccept}
        <form
          method="POST"
          action="?/accept_suggestion"
          class="mbs-1"
          use:enhance={() => {
            accepting = true;
            return async ({ update }) => {
              await update();
              accepting = false;
            };
          }}
        >
          <input type="hidden" name="id" value={comment.id} />
          <Button type="submit" size="sm" disabled={accepting}
            >{m.discussion_accept()}</Button
          >
        </form>
      {/if}
    </div>
  {/if}
  {#if mode === "read" && (canReply || comment.mine || canModerate)}
    <div class="flex gap-3 text-xs text-muted-foreground">
      {#if canReply && !hidden}
        <button
          type="button"
          class="min-block-6 hover:text-foreground"
          onclick={() => (mode = "reply")}>{m.discussion_reply()}</button
        >
      {/if}
      {#if comment.mine && !hidden}
        <button
          type="button"
          class="min-block-6 hover:text-foreground"
          onclick={() => (mode = "edit")}>{m.discussion_edit()}</button
        >
      {/if}
      {#if canModerate}
        {#if hidden}
          <form method="POST" action="?/hide_comment" use:enhance>
            <input type="hidden" name="id" value={comment.id} />
            <input type="hidden" name="restore" value="1" />
            <button type="submit" class="min-block-6 hover:text-foreground"
              >{m.discussion_restore()}</button
            >
          </form>
        {:else}
          <button
            type="button"
            class="min-block-6 hover:text-foreground"
            onclick={() => (mode = "hide")}>{m.discussion_hide()}</button
          >
        {/if}
      {/if}
    </div>
  {/if}
  {#if mode === "hide"}
    <form
      method="POST"
      action="?/hide_comment"
      class="flex flex-wrap items-center gap-2"
      use:enhance={() =>
        async ({ update }) => {
          await update();
          mode = "read";
          reason = "";
        }}
    >
      <input type="hidden" name="id" value={comment.id} />
      <Input
        name="reason"
        bind:value={reason}
        maxlength={500}
        class="flex-1 text-sm block-8 min-inline-48"
        aria-label={m.discussion_hide_reason()}
        placeholder={m.discussion_hide_reason()}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onclick={() => (mode = "read")}>{m.discussion_cancel()}</Button
      >
      <Button
        type="submit"
        variant="outline"
        size="sm"
        disabled={reason.trim() === ""}>{m.discussion_hide()}</Button
      >
    </form>
  {/if}
  {#if replies.length > 0 || mode === "reply"}
    <div class="mbs-2 grid gap-4 border-s ps-4">
      {#each replies as reply (reply.id)}
        <CommentItem
          comment={reply}
          {head}
          canReply={false}
          {canModerate}
          showAnchor={false}
        />
      {/each}
      {#if mode === "reply"}
        <CommentComposer
          action="?/comment"
          parent={comment.id}
          placeholder={m.discussion_reply_placeholder()}
          submit={m.discussion_reply()}
          autofocus
          onclose={() => (mode = "read")}
        />
      {/if}
    </div>
  {/if}
</article>
