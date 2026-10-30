<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import ProposalBody from "#routes/lab/contrib/proposal-body.svelte";
  import { resolve } from "$app/paths";
  import * as AlertDialog from "#lib/components/ui/alert-dialog/index.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { cn } from "#lib/utils.js";
  import { copy } from "#routes/lab/contrib/copy.js";
  import { proposals } from "#routes/lab/contrib/data.js";

  const proposal = proposals[0];

  type Comment = {
    author: string;
    role: string;
    when: string;
    anchor?: string;
    text: string;
    revision: number;
  };

  let view = $state<"current" | "since">("current");
  const comments = $state<Array<Comment>>([
    {
      author: "desk",
      role: copy.role_desk,
      when: "Oct 30, 09:40",
      anchor: copy.anchor_kankakee,
      text: copy.comment_desk_1,
      revision: 1,
    },
    {
      author: "ana-k",
      role: copy.role_author,
      when: "Oct 30, 10:02",
      text: copy.comment_author,
      revision: 2,
    },
    {
      author: "desk",
      role: copy.role_desk,
      when: "Oct 30, 10:31",
      text: copy.comment_desk_2,
      revision: 2,
    },
  ]);
  let draft = $state("");
  let decision = $state<"approve" | "changes" | "reject">("approve");
  let reason = $state("");
  let phase = $state<
    "open" | "published" | "changes" | "rejected" | "reverted"
  >("open");
  let revertOpen = $state(false);
  let revertReason = $state("");

  const status = $derived(
    {
      open: copy.state_open,
      published: copy.state_published,
      changes: copy.state_changes,
      rejected: copy.state_rejected,
      reverted: copy.state_reverted,
    }[phase],
  );

  const decisions = [
    ["approve", copy.decide_approve],
    ["changes", copy.decide_changes],
    ["reject", copy.decide_reject],
  ] as const;

  function post() {
    if (draft.trim() === "") return;
    comments.push({
      author: "editor",
      role: copy.role_reviewer,
      when: copy.just_now,
      text: draft.trim(),
      revision: 2,
    });
    draft = "";
  }

  function decide() {
    if (reason.trim() === "") return;
    comments.push({
      author: "editor",
      role: copy.role_reviewer,
      when: copy.just_now,
      text: reason.trim(),
      revision: 2,
    });
    phase =
      decision === "approve"
        ? "published"
        : decision === "changes"
          ? "changes"
          : "rejected";
    reason = "";
  }

  function revert() {
    if (revertReason.trim() === "") return;
    comments.push({
      author: "editor",
      role: copy.role_reviewer,
      when: copy.just_now,
      text: revertReason.trim(),
      revision: 2,
    });
    phase = "reverted";
    revertOpen = false;
    revertReason = "";
  }
</script>

<svelte:head>
  <title>{copy.proposal_page_title}</title>
</svelte:head>

<div class="contrib mx-auto px-4 pbs-6 pbe-24 max-inline-4xl max-sm:px-3">
  <a
    class="text-sm text-muted-foreground hover:text-foreground"
    href={resolve("/lab/contrib")}>{copy.lab_back}</a
  >

  <header class="mbs-5 grid gap-3">
    <p class="flex flex-wrap items-baseline gap-x-4 text-sm">
      <span class="font-figures font-semibold">#{proposal.number}</span>
      <span class={cn({ "font-semibold": phase !== "open" })}>{status}</span>
      <span class="text-muted-foreground"
        >{copy.tier_kind(proposal.tier, proposal.kind)}</span
      >
    </p>
    <h1 class="title">{proposal.title}</h1>
    <p class="text-sm text-muted-foreground">{copy.byline}</p>
  </header>

  <div
    class="mbs-6 flex flex-wrap items-center gap-1 border-be text-sm"
    role="tablist"
  >
    <button
      type="button"
      role="tab"
      aria-selected={view === "current"}
      class={cn("px-2 py-1.5", {
        "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
          view === "current",
        "text-muted-foreground hover:text-foreground": view !== "current",
      })}
      onclick={() => (view = "current")}>{copy.revision_current}</button
    >
    <button
      type="button"
      role="tab"
      aria-selected={view === "since"}
      class={cn("px-2 py-1.5", {
        "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
          view === "since",
        "text-muted-foreground hover:text-foreground": view !== "since",
      })}
      onclick={() => (view = "since")}>{copy.revision_since}</button
    >
  </div>

  <div class="mbs-5">
    <ProposalBody {proposal} since={view === "since"} />
  </div>

  <section class="mbs-10" aria-labelledby="discussion">
    <h2 id="discussion" class="heading">
      {copy.discussion}
      <span class="font-figures text-muted-foreground">{comments.length}</span>
    </h2>
    <ol class="mbs-3 grid border-bs">
      {#each comments as comment, i (i)}
        <li class="contrib-settle grid gap-1 border-be py-3 text-sm">
          <p class="flex flex-wrap items-baseline gap-x-3">
            <span class="font-semibold">{comment.author}</span>
            <span class="text-muted-foreground">{comment.role}</span>
            <span class="font-figures text-muted-foreground"
              >{comment.when}</span
            >
            <span class="text-muted-foreground"
              >{copy.on_revision(comment.revision)}</span
            >
          </p>
          {#if comment.anchor != null}
            <p
              class="border-s-2 border-border ps-2 text-xs text-muted-foreground"
            >
              {comment.anchor}
            </p>
          {/if}
          <p class="text-pretty max-inline-[64ch]">{comment.text}</p>
        </li>
      {/each}
    </ol>
    <div class="mbs-3 grid gap-2">
      <Textarea
        bind:value={draft}
        rows={2}
        placeholder={copy.comment_placeholder}
        aria-label={copy.comment_label}
      />
      <Button
        variant="outline"
        class="justify-self-end"
        disabled={draft.trim() === ""}
        onclick={post}>{copy.comment_post}</Button
      >
    </div>
  </section>

  <section
    class="mbs-10 border-bs-2 border-foreground pbs-4"
    aria-labelledby="decision"
  >
    <h2 id="decision" class="heading">{copy.decision_title}</h2>
    {#if phase === "open"}
      <div
        class="mbs-3 flex flex-wrap gap-1"
        role="radiogroup"
        aria-label={copy.decision_title}
      >
        {#each decisions as [key, label] (key)}
          <button
            type="button"
            role="radio"
            aria-checked={decision === key}
            class={cn("rounded-sm border px-3 py-1.5 text-sm", {
              "border-foreground font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                decision === key,
              "border-input text-muted-foreground hover:text-foreground":
                decision !== key,
            })}
            onclick={() => (decision = key)}>{label}</button
          >
        {/each}
      </div>
      <Textarea
        class="mbs-3"
        bind:value={reason}
        rows={2}
        placeholder={copy.reason_placeholder}
        aria-label={copy.reason_label}
        aria-required="true"
      />
      <div class="mbs-2 flex items-center justify-between gap-3">
        <p class="text-xs text-muted-foreground">{copy.reason_hint}</p>
        <Button disabled={reason.trim() === ""} onclick={decide}
          >{decisions.find(([key]) => key === decision)?.[1]}</Button
        >
      </div>
    {:else if phase === "published"}
      <p class="mbs-3 text-sm">{copy.published_line}</p>
      <p class="mbs-1 font-figures text-sm text-muted-foreground">
        cs_01JBR7Q4K9M2X8T5W3N6Y0VZHD
      </p>
      <ul class="mbs-3 grid gap-1 text-sm">
        <li>
          <a
            class="underline decoration-border underline-offset-4 hover:decoration-current"
            href={resolve("/lab/contrib/panel")}
            >{copy.published_link_contest}</a
          >
        </li>
        <li class="text-muted-foreground">{copy.published_link_counties}</li>
      </ul>
      <Button
        variant="outline"
        class="mbs-4"
        onclick={() => (revertOpen = true)}>{copy.revert}</Button
      >
    {:else}
      <p class="mbs-3 text-sm">
        {phase === "reverted"
          ? copy.reverted_line
          : phase === "changes"
            ? copy.changes_line
            : copy.rejected_line}
      </p>
      <Button variant="link" class="mbs-2 px-0" onclick={() => (phase = "open")}
        >{copy.reset_mock}</Button
      >
    {/if}
  </section>
</div>

<AlertDialog.Root bind:open={revertOpen}>
  <AlertDialog.Content>
    <AlertDialog.Header>
      <AlertDialog.Title>{copy.revert_title}</AlertDialog.Title>
      <AlertDialog.Description>{copy.revert_body}</AlertDialog.Description>
    </AlertDialog.Header>
    <Textarea
      bind:value={revertReason}
      rows={2}
      placeholder={copy.reason_placeholder}
      aria-label={copy.reason_label}
    />
    <AlertDialog.Footer>
      <AlertDialog.Cancel>{copy.cancel}</AlertDialog.Cancel>
      <AlertDialog.Action disabled={revertReason.trim() === ""} onclick={revert}
        >{copy.revert}</AlertDialog.Action
      >
    </AlertDialog.Footer>
  </AlertDialog.Content>
</AlertDialog.Root>
