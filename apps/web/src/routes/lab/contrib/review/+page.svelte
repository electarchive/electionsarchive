<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import ProposalBody from "#routes/lab/contrib/proposal-body.svelte";
  import { resolve } from "$app/paths";
  import { SvelteMap } from "svelte/reactivity";
  import type { Proposal } from "#routes/lab/contrib/data.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Dialog from "#lib/components/ui/dialog/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { cn } from "#lib/utils.js";
  import { copy } from "#routes/lab/contrib/copy.js";
  import { proposals } from "#routes/lab/contrib/data.js";

  type Queue = "tier3" | "tier2" | "escalated" | "spam" | "mine";
  type Verdict = "approve" | "changes" | "reject";

  const queues: Array<[Queue, string, (p: Proposal) => boolean]> = [
    ["tier3", copy.queue_tier3, (p) => p.tier === 3 && p.prescreen === "clean"],
    ["tier2", copy.queue_tier2, (p) => p.tier === 2 && p.prescreen === "clean"],
    [
      "escalated",
      copy.queue_escalated,
      (p) => p.tier === 1 && p.desk === "escalated",
    ],
    ["spam", copy.queue_spam, (p) => p.prescreen !== "clean"],
    ["mine", copy.queue_mine, () => false],
  ];

  const tabs: Array<[Queue | "all", string, (p: Proposal) => boolean]> = [
    ["all", copy.queue_all, () => true],
    ...queues,
  ];

  let queue = $state<Queue | "all">("all");
  const decided = new SvelteMap<number, Verdict>();
  let cursor = $state(proposals[0].number);
  let dialog = $state<Verdict | null>(null);
  let reason = $state("");
  let last = $state<{ number: number; verdict: Verdict } | null>(null);
  let filter = $state("");

  const pending = $derived(
    proposals.filter(
      (p) =>
        !decided.has(p.number) &&
        (p.tier > 1 || p.desk === "escalated" || p.prescreen !== "clean"),
    ),
  );
  const visible = $derived(
    pending
      .filter(
        (p) =>
          queue === "all" ||
          (queues.find(([key]) => key === queue)?.[2](p) ?? false),
      )
      .filter(
        (p) =>
          filter.trim() === "" ||
          `${p.title} ${p.author} ${p.polity}`
            .toLowerCase()
            .includes(filter.trim().toLowerCase()),
      )
      .toSorted((a, b) => b.tier - a.tier || a.number - b.number),
  );
  const current = $derived(
    visible.find((p) => p.number === cursor) ?? visible[0],
  );
  const deskToday = $derived(
    proposals.filter((p) => p.tier === 1 && p.desk === "approve").length + 58,
  );

  const verbs: Record<Verdict, string> = {
    approve: copy.decide_approve,
    changes: copy.decide_changes,
    reject: copy.decide_reject,
  };
  const done: Record<Verdict, string> = {
    approve: copy.done_approve,
    changes: copy.done_changes,
    reject: copy.done_reject,
  };

  function step(by: number) {
    if (visible.length === 0) return;
    const index = visible.findIndex((p) => p.number === current?.number);
    const next = visible[Math.min(visible.length - 1, Math.max(0, index + by))];
    if (next == null) return;
    cursor = next.number;
    document
      .getElementById(`q-${next.number}`)
      ?.scrollIntoView({ block: "nearest" });
  }

  function confirm() {
    if (current == null || dialog == null || reason.trim() === "") return;
    const { number } = current;
    const index = visible.findIndex((p) => p.number === number);
    const next = visible[index + 1] ?? visible[index - 1];
    decided.set(number, dialog);
    last = { number, verdict: dialog };
    if (next != null) cursor = next.number;
    dialog = null;
    reason = "";
  }

  function undo() {
    if (last == null) return;
    decided.delete(last.number);
    cursor = last.number;
    last = null;
  }

  function onkeydown(event: KeyboardEvent) {
    const target = event.target;
    if (
      dialog != null ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement
    )
      return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const actions: Record<string, () => void> = {
      j: () => step(1),
      k: () => step(-1),
      a: () => (dialog = "approve"),
      c: () => (dialog = "changes"),
      x: () => (dialog = "reject"),
      o: () => window.open(resolve("/lab/contrib/proposal"), "_self"),
      "/": () => document.getElementById("review-filter")?.focus(),
      u: undo,
    };
    const action = actions[event.key];
    if (action == null) return;
    event.preventDefault();
    action();
  }
</script>

<svelte:window {onkeydown} />

<svelte:head>
  <title>{copy.review_page_title}</title>
</svelte:head>

<div class="contrib px-4 pbs-6 pbe-10 max-sm:px-3">
  <a
    class="text-sm text-muted-foreground hover:text-foreground"
    href={resolve("/lab/contrib")}>{copy.lab_back}</a
  >
  <div class="mbs-3 flex flex-wrap items-end justify-between gap-3">
    <h1 class="title">{copy.review_title}</h1>
    <p class="text-sm text-muted-foreground">{copy.desk_today(deskToday)}</p>
  </div>

  <div
    class="mbs-5 flex flex-wrap items-center gap-x-1 gap-y-2 border-be text-sm max-sm:border-be-0"
  >
    <nav
      class="flex gap-x-1 overflow-x-auto whitespace-nowrap max-sm:border-be max-sm:inline-full"
      aria-label={copy.review_title}
    >
      {#each tabs as [key, label, test] (key)}
        {const n = $derived(pending.filter(test).length)}
        <button
          type="button"
          aria-current={queue === key ? "page" : undefined}
          class={cn("px-2 py-1.5", {
            "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
              queue === key,
            "text-muted-foreground hover:text-foreground": queue !== key,
          })}
          onclick={() => (queue = key)}
          >{label} <span class="font-figures">{n}</span></button
        >
      {/each}
    </nav>
    <input
      id="review-filter"
      bind:value={filter}
      class="ms-auto mbe-1 field px-2 text-sm block-7 inline-56 max-sm:inline-full"
      placeholder={copy.filter_placeholder}
      aria-label={copy.filter_placeholder}
    />
  </div>

  <div class="grid min-block-136 lg:grid-cols-[minmax(0,25rem)_minmax(0,1fr)]">
    <ul
      class="border-be lg:border-e lg:border-be-0"
      aria-label={copy.review_list}
    >
      {#each visible as p (p.number)}
        <li id="q-{p.number}">
          <button
            type="button"
            class={cn(
              "grid gap-0.5 border-be px-3 py-2 text-start text-sm inline-full",
              {
                "bg-muted shadow-[inset_2px_0_0_var(--foreground)]":
                  current?.number === p.number,
                "hover:bg-muted/60": current?.number !== p.number,
              },
            )}
            aria-current={current?.number === p.number}
            onclick={() => (cursor = p.number)}
          >
            <span
              class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-2"
            >
              <span class="font-figures text-muted-foreground">#{p.number}</span
              >
              <span class="truncate font-semibold">{p.title}</span>
              <span class="text-xs">{copy.tier_short(p.tier)}</span>
            </span>
            <span class="ps-12 text-xs text-muted-foreground"
              >{copy.row_meta(p.author, p.level, p.age)}</span
            >
            <span
              class={cn("ps-12 text-xs", {
                "font-semibold":
                  p.prescreen !== "clean" || p.checks.findings > 0,
              })}
            >
              {p.prescreen === "spam"
                ? copy.prescreen_spam
                : copy.row_desk(p.desk, p.checks.findings)}
            </span>
          </button>
        </li>
      {:else}
        <li class="px-3 py-8 text-sm text-muted-foreground">
          {copy.queue_empty}
        </li>
      {/each}
    </ul>

    <section class="py-4 min-inline-0 lg:px-6" aria-label={copy.preview}>
      {#if current != null}
        <p class="flex flex-wrap items-baseline gap-x-4 text-sm">
          <span class="font-figures font-semibold">#{current.number}</span>
          <span class="text-muted-foreground"
            >{copy.tier_kind(current.tier, current.kind)}</span
          >
          {#if current.agent != null}
            <span class="text-muted-foreground"
              >{copy.with_agent(current.agent.name, current.agent.model)}</span
            >
          {/if}
        </p>
        <h2 class="mbs-1 heading">{current.title}</h2>
        <div class="mbs-3 flex flex-wrap items-center gap-2">
          <Button onclick={() => (dialog = "approve")}
            >{copy.decide_approve} <kbd>A</kbd></Button
          >
          <Button variant="outline" onclick={() => (dialog = "changes")}
            >{copy.decide_changes} <kbd>C</kbd></Button
          >
          <Button variant="outline" onclick={() => (dialog = "reject")}
            >{copy.decide_reject} <kbd>X</kbd></Button
          >
          <Button variant="link" href={resolve("/lab/contrib/proposal")}
            >{copy.open_full} <kbd>O</kbd></Button
          >
        </div>
        <div class="mbs-5 border-bs pbs-4">
          {#key current.number}
            <ProposalBody proposal={current} compact />
          {/key}
        </div>
      {/if}
    </section>
  </div>

  <footer
    class={cn(
      "sticky inset-be-0 flex flex-wrap items-center gap-x-5 gap-y-1 border-bs bg-background py-2 text-xs text-muted-foreground",
      { "max-sm:hidden": last == null },
    )}
  >
    {#if last != null}
      <span class="font-semibold text-foreground"
        >{copy.decided_line(last.number, done[last.verdict])}</span
      >
      <Button variant="link" size="xs" class="px-0" onclick={undo}
        >{copy.undo} <kbd>U</kbd></Button
      >
    {/if}
    <span class="ms-auto max-sm:hidden">{copy.keys_line}</span>
  </footer>
</div>

<Dialog.Root
  open={dialog != null}
  onOpenChange={(value) => {
    if (!value) dialog = null;
  }}
>
  <Dialog.Content class="sm:max-inline-md">
    <Dialog.Header>
      <Dialog.Title
        >{dialog == null
          ? ""
          : `${verbs[dialog]} #${current?.number}`}</Dialog.Title
      >
      <Dialog.Description>{copy.reason_hint}</Dialog.Description>
    </Dialog.Header>
    <Textarea
      bind:value={reason}
      rows={3}
      placeholder={copy.reason_placeholder}
      aria-label={copy.reason_label}
      onkeydown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter")
          confirm();
      }}
    />
    <Dialog.Footer>
      <Button variant="outline" onclick={() => (dialog = null)}
        >{copy.cancel}</Button
      >
      <Button disabled={reason.trim() === ""} onclick={confirm}
        >{dialog == null ? "" : verbs[dialog]} <kbd>⌘↵</kbd></Button
      >
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
