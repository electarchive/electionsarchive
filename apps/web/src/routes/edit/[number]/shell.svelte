<script lang="ts">
  import type { Snippet } from "svelte";
  import { resolve } from "$app/paths";
  import "#lib/components/contrib/contrib.css";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { tierLabel } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { Session } from "./session.svelte.js";

  let {
    session,
    number,
    heading,
    summary = $bindable(),
    blocker,
    failure,
    outline,
    editor,
    diff,
    evidence,
    commands,
  }: {
    session: Session;
    number: number;
    heading: string;
    summary: string;
    blocker: string | null;
    failure: {
      label: string | null;
      text: string;
      pick: (() => void) | null;
    } | null;
    outline: Snippet;
    editor: Snippet;
    diff: Snippet;
    evidence: Snippet;
    commands?: Snippet<[(action: () => void) => void]>;
  } = $props();

  const tabs = [
    ["diff", m.ws_tab_diff],
    ["checks", m.ws_tab_checks],
    ["evidence", m.ws_tab_evidence],
  ] as const;

  const check = $derived(session.check);

  const clock = (value: string) =>
    new Intl.DateTimeFormat(getLocale(), { timeStyle: "short" }).format(
      new Date(value.replace(" ", "T").replace(/([+-]\d{2})$/u, "$1:00")),
    );

  function onkeydowncapture(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      event.stopPropagation();
      session.palette = !session.palette;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void session.runChecks();
    }
  }

  function act(action: () => void) {
    session.palette = false;
    action();
  }
</script>

<svelte:window {onkeydowncapture} />

<svelte:head>
  <title>{m.ws_draft_number({ number })} | {m.wordmark()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div
  class="fixed inset-0 z-50 grid grid-rows-[auto_minmax(0,1fr)_auto] bg-background text-sm"
>
  <header
    class="flex flex-wrap items-center gap-x-4 gap-y-1 border-be px-4 py-2"
  >
    <a
      class="text-muted-foreground hover:text-foreground"
      href={resolve("/edit")}>{m.ws_back()}</a
    >
    <h1 class="flex items-baseline gap-2 font-bold min-inline-0">
      <span class="shrink-0 font-figures text-muted-foreground"
        >{m.ws_draft_number({ number })}</span
      >
      <span class="truncate">{summary.trim() || heading}</span>
    </h1>
    <p
      class="ms-auto flex items-baseline gap-4 text-muted-foreground"
      aria-live="polite"
    >
      <span
        class={cn({
          "font-semibold text-destructive": session.saveError != null,
        })}>{session.saveStatus}</span
      >
      {#if check?.tier != null && !session.isStale}
        <span class="font-figures">{m.ws_tier({ tier: check.tier })}</span>
      {/if}
      {#if session.findingCount > 0}
        <button
          type="button"
          class="font-semibold text-foreground"
          onclick={() => (session.tab = "checks")}
          >{m.proposal_findings({ count: session.findingCount })}</button
        >
      {/if}
    </p>
  </header>

  <div
    class="grid grid-cols-1 overflow-y-auto min-block-0 lg:grid-cols-[15rem_minmax(0,1fr)_22rem] lg:overflow-hidden"
  >
    <nav
      class="border-be lg:overflow-y-auto lg:border-e lg:border-be-0 max-lg:hidden"
      aria-label={m.ws_outline()}
    >
      <h2 class="px-4 pbs-3 pbe-1 text-xs text-muted-foreground">
        {m.ws_outline()}
      </h2>
      {@render outline()}
    </nav>

    <main class="lg:overflow-y-auto lg:min-block-0">
      {@render editor()}
    </main>

    <aside
      class="grid grid-rows-[auto_minmax(0,1fr)] border-bs lg:border-s lg:border-bs-0 lg:min-block-0"
      aria-label={m.ws_inspector()}
    >
      <div class="flex gap-1 border-be px-3 pbs-2" role="tablist">
        {#each tabs as [key, label] (key)}
          <button
            type="button"
            role="tab"
            id="ws-tab-{key}"
            aria-selected={session.tab === key}
            aria-controls="ws-panel"
            class={cn("px-2 py-1.5", {
              "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                session.tab === key,
              "text-muted-foreground hover:text-foreground":
                session.tab !== key,
            })}
            onclick={() => (session.tab = key)}
            >{label()}{#if key === "checks" && session.findingCount > 0}<span
                class="ms-1 font-figures">{session.findingCount}</span
              >{/if}</button
          >
        {/each}
      </div>
      <div
        id="ws-panel"
        class="overflow-y-auto p-4"
        role="tabpanel"
        aria-labelledby="ws-tab-{session.tab}"
      >
        {#if session.tab === "diff"}
          {@render diff()}
        {:else if session.tab === "checks"}
          {#if session.isChecking}
            <p class="text-muted-foreground" role="status">
              {m.ws_checks_running()}
            </p>
          {:else if session.checkError != null}
            <p class="font-semibold text-destructive">{session.checkError}</p>
          {:else if check == null}
            <p class="text-pretty text-muted-foreground">
              {m.ws_checks_idle()}
            </p>
          {:else}
            <div class="grid gap-3">
              <p class="text-xs text-muted-foreground">
                {#if check.finished_at != null}{m.ws_checks_at({
                    time: clock(check.finished_at),
                  })}{/if}
                {#if session.isStale}
                  <span class="block font-semibold text-foreground"
                    >{m.ws_checks_stale()}</span
                  >
                {/if}
              </p>
              {#if check.status === "failed"}
                <div class="border-s-2 border-destructive ps-3">
                  <p class="font-semibold">{m.ws_checks_failed()}</p>
                  {#if failure != null}
                    <p class="mbs-1 text-pretty">
                      {#if failure.label != null && failure.pick != null}
                        <button
                          type="button"
                          class="font-semibold underline underline-offset-4"
                          onclick={failure.pick}>{failure.label}</button
                        >:
                      {/if}
                      {failure.text}
                    </p>
                  {/if}
                </div>
              {:else if check.status === "done"}
                {#if check.tier != null}
                  <p>
                    <span class="font-semibold"
                      >{m.ws_tier({ tier: check.tier })}</span
                    >
                    <span class="text-muted-foreground"
                      >{tierLabel[check.tier]?.()}</span
                    >
                  </p>
                {/if}
                {#if session.findings.length === 0}
                  <p class="border-s-2 border-foreground ps-3 font-semibold">
                    {m.ws_checks_passed()}
                  </p>
                {:else}
                  <ul class="grid border-s-2 border-foreground">
                    {#each session.findings as finding (finding.code)}
                      <li
                        class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-2 border-be py-1.5 ps-3"
                      >
                        <span class="text-pretty">{finding.label}</span>
                        <span class="font-figures font-semibold"
                          >{finding.value}</span
                        >
                      </li>
                    {/each}
                  </ul>
                {/if}
              {:else}
                <p class="text-muted-foreground">{m.ws_checks_running()}</p>
              {/if}
            </div>
          {/if}
        {:else}
          {@render evidence()}
        {/if}
      </div>
    </aside>
  </div>

  <footer class="flex flex-wrap items-center gap-3 border-bs px-4 py-2.5">
    <Button
      variant="ghost"
      size="sm"
      class="max-sm:hidden"
      onclick={() => (session.palette = true)}
      >{m.ws_commands()} <kbd>⌘K</kbd></Button
    >
    <Button
      variant="outline"
      size="sm"
      disabled={session.isLocked || session.isChecking}
      onclick={() => void session.runChecks()}
      >{m.ws_run_checks()} <kbd class="max-sm:hidden">⌘↵</kbd></Button
    >
    <Input
      bind:value={summary}
      disabled={session.isLocked}
      class="flex-1 basis-64 block-8 min-inline-0"
      aria-label={m.edit_summary()}
      placeholder={m.edit_summary_placeholder()}
      maxlength={300}
    />
    {#if session.submitted != null}
      <p class="grid gap-0.5" role="status">
        <span class="font-semibold"
          >{m.ws_submitted({ number: session.submitted })}</span
        >
        <a
          class="underline underline-offset-4"
          href={resolve("/proposals/[number]", {
            number: String(session.submitted),
          })}>{m.ws_go_proposal()}</a
        >
        {#if session.submitError != null}
          <span class="text-xs font-semibold text-destructive"
            >{session.submitError}</span
          >
        {/if}
      </p>
    {:else}
      <div class="flex items-center gap-3">
        {#if session.submitError != null || blocker != null}
          <span
            class={cn("text-xs text-pretty max-inline-56", {
              "font-semibold text-destructive": session.submitError != null,
              "text-muted-foreground": session.submitError == null,
            })}>{session.submitError ?? blocker}</span
          >
        {/if}
        <Button
          size="sm"
          disabled={blocker != null || session.isSubmitting}
          onclick={() => void session.submit(blocker)}>{m.ws_submit()}</Button
        >
      </div>
    {/if}
  </footer>
</div>

<Command.Dialog
  bind:open={session.palette}
  title={m.ws_commands()}
  description={m.ws_commands_hint()}
  class="sm:max-inline-lg"
>
  <Command.Input placeholder={m.ws_commands_hint()} />
  <Command.List>
    <Command.Empty>{m.ws_commands_none()}</Command.Empty>
    <Command.Group>
      <Command.Item
        disabled={session.isLocked}
        onSelect={() => act(() => void session.runChecks())}
        >{m.ws_run_checks()}<Command.Shortcut>⌘↵</Command.Shortcut
        ></Command.Item
      >
      {@render commands?.(act)}
      {#if failure?.label != null && failure.pick != null}
        {const pick = $derived(failure.pick)}
        <Command.Item onSelect={() => act(pick)}>{failure.label}</Command.Item>
      {/if}
      {#each tabs as [key, label] (key)}
        <Command.Item onSelect={() => act(() => (session.tab = key))}
          >{m.ws_inspector()}: {label()}</Command.Item
        >
      {/each}
      <Command.Item
        disabled={blocker != null || session.isLocked}
        onSelect={() => act(() => void session.submit(blocker))}
        >{m.ws_submit()}</Command.Item
      >
      <Command.LinkItem href={resolve("/edit")}>{m.ws_back()}</Command.LinkItem>
    </Command.Group>
  </Command.List>
</Command.Dialog>
