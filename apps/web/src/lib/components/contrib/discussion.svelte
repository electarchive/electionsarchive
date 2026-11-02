<script lang="ts">
  import { page } from "$app/state";
  import type { CommentView } from "#lib/contrib.js";
  import CommentComposer from "#lib/components/contrib/comment-composer.svelte";
  import CommentItem from "#lib/components/contrib/comment-item.svelte";
  import { threaded } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    comments,
    head = null,
    commenter,
    canComment,
    canModerate,
    canAccept = false,
    empty = m.discussion_empty(),
  }: {
    comments: Array<CommentView>;
    head?: number | null;
    commenter: "signed_out" | "ready" | "unverified";
    canComment: boolean;
    canModerate: boolean;
    canAccept?: boolean;
    empty?: string;
  } = $props();

  const thread = $derived(threaded(comments));
</script>

<section class="mbs-10" aria-labelledby="discussion">
  <div class="flex items-baseline justify-between gap-4">
    <h2 id="discussion" class="heading">{m.discussion_heading()}</h2>
    {#if comments.length > 0}
      <span class="font-figures text-xs text-muted-foreground"
        >{m.discussion_count({ count: comments.length })}</span
      >
    {/if}
  </div>
  {#if thread.roots.length === 0}
    <p class="mbs-2 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {empty}
    </p>
  {:else}
    <ol class="mbs-2 grid border-bs">
      {#each thread.roots as comment (comment.id)}
        <li class="border-be py-4">
          <CommentItem
            {comment}
            replies={thread.replies.get(comment.id) ?? []}
            {head}
            canReply={canComment}
            {canModerate}
            {canAccept}
          />
        </li>
      {/each}
    </ol>
  {/if}
  <div class="mbs-4">
    {#if canComment}
      <CommentComposer action="?/comment" />
    {:else if commenter === "signed_out"}
      <p class="text-sm">
        <a
          class="underline underline-offset-2"
          href="/sign-in?next={encodeURIComponent(page.url.pathname)}"
          >{m.discussion_sign_in()}</a
        >
      </p>
    {:else if commenter === "unverified"}
      <p class="text-sm text-muted-foreground">
        {m.discussion_verify()}
        <a
          class="text-foreground underline underline-offset-2"
          href="/verify-email?next={encodeURIComponent(page.url.pathname)}"
          >{m.discussion_verify_send()}</a
        >
      </p>
    {/if}
  </div>
</section>
