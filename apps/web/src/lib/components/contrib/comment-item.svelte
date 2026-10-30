<script lang="ts">
  import type { CommentView } from "#lib/contrib.js";
  import { enhance } from "$app/forms";
  import CommentItem from "#lib/components/contrib/comment-item.svelte";
  import CommentComposer from "#lib/components/contrib/comment-composer.svelte";
  import Markdown from "#lib/components/markdown.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { anchorId } from "#lib/contrib.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    comment,
    replies = [],
    head,
    canReply,
    canModerate,
    showAnchor = true,
  }: {
    comment: CommentView;
    replies?: Array<CommentView>;
    head: number | null;
    canReply: boolean;
    canModerate: boolean;
    showAnchor?: boolean;
  } = $props();

  let mode = $state<"read" | "edit" | "reply" | "hide">("read");
  let reason = $state("");

  const author = $derived(comment.author ?? m.proposal_unknown_author());
  const hidden = $derived(comment.hidden_reason != null);
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
      ><time datetime={comment.created_at}
        >{date(comment.created_at.slice(0, 10))}</time
      >{#if comment.edited_at != null}
        <span> ({m.discussion_edited()})</span>{/if}</a
    >
  </header>
  {#if (showAnchor && comment.anchor != null) || (comment.revision_n != null && head != null && comment.revision_n !== head)}
    <p class="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
      {#if showAnchor && comment.anchor != null}
        <a
          class="border-s-2 border-foreground ps-1.5 text-foreground underline-offset-2 hover:underline"
          href="#{anchorId(comment.anchor)}"
          >{m.discussion_on({ label: comment.anchor.label })}</a
        >
      {/if}
      {#if comment.revision_n != null && head != null && comment.revision_n !== head}
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
  {#if mode === "read" && (canReply || comment.mine || canModerate)}
    <div class="flex gap-3 text-xs text-muted-foreground">
      {#if canReply && !hidden}
        <button
          type="button"
          class="hover:text-foreground"
          onclick={() => (mode = "reply")}>{m.discussion_reply()}</button
        >
      {/if}
      {#if comment.mine && !hidden}
        <button
          type="button"
          class="hover:text-foreground"
          onclick={() => (mode = "edit")}>{m.discussion_edit()}</button
        >
      {/if}
      {#if canModerate}
        {#if hidden}
          <form method="POST" action="?/hide_comment" use:enhance>
            <input type="hidden" name="id" value={comment.id} />
            <input type="hidden" name="restore" value="1" />
            <button type="submit" class="hover:text-foreground"
              >{m.discussion_restore()}</button
            >
          </form>
        {:else}
          <button
            type="button"
            class="hover:text-foreground"
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
        class="h-8 flex-1 text-sm min-inline-48"
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
