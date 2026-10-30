<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import X from "@lucide/svelte/icons/x";
  import type { Editable } from "#lib/contrib.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import UploadEvidence from "#lib/components/contrib/upload-evidence.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import * as ToggleGroup from "#lib/components/ui/toggle-group/index.js";
  import {
    kindLabel,
    sourceKindLabel,
    sourceKinds,
    stateLabel,
  } from "#lib/contrib.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    entity,
    label,
    title,
  }: { entity: string; label: string; title: string } = $props();

  const open = $derived(page.url.searchParams.has("edit"));
  const signedIn = $derived(page.data.user != null);

  let data = $state<Editable | null>(null);
  let failed = $state<string | null>(null);
  let sheet = $state<"peek" | "half" | "full">("half");
  let drafts = $state<Record<string, string>>({});
  let source = $state<string | null>(null);
  let mode = $state<"source" | "url" | "upload">("source");
  let link = $state({ url: "", title: "", publisher: "", kind: "" });
  let upload = $state<{
    id: string;
    page: number | null;
    region: [number, number, number, number] | null;
  } | null>(null);
  let quote = $state("");
  let summary = $state("");
  let submitting = $state(false);
  let submitted = $state<number | null>(null);

  $effect(() => {
    if (!open) {
      return;
    }
    const controller = new AbortController();
    void fetch(`/proposals/new?entity=${entity}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(response.statusText);
        }
        data = (await response.json()) as Editable;
      })
      .catch((error_: unknown) => {
        if (!controller.signal.aborted) {
          failed = error_ instanceof Error ? error_.message : String(error_);
        }
      });
    return () => {
      controller.abort();
    };
  });

  const changed = $derived(
    (data?.names ?? []).filter(
      (name) => (drafts[name.id] ?? name.text).trim() !== name.text,
    ),
  );
  const sources = $derived(
    (data?.sources ?? []).map((item) => ({
      value: item.id,
      label: item.title,
      detail: item.publisher ?? undefined,
    })),
  );
  const modes = $derived(
    sources.length === 0
      ? (["url", "upload"] as const)
      : (["source", "url", "upload"] as const),
  );
  const shown = $derived(
    mode === "source" && sources.length === 0 ? "url" : mode,
  );
  const modeLabel = {
    source: m.edit_mode_source,
    url: m.edit_mode_url,
    upload: m.edit_mode_upload,
  };
  const chosen = $derived(sourceKinds.find((kind) => kind === link.kind));
  const described = $derived(
    [link.title, link.publisher, link.kind].every(
      (value) => value.trim() !== "",
    ),
  );
  const cited = $derived(
    shown === "source"
      ? source != null
      : shown === "url"
        ? described && link.url.trim() !== ""
        : described && upload != null,
  );
  const ready = $derived(
    changed.length === 1 && cited && summary.trim() !== "",
  );

  const close = () => {
    drafts = {};
    source = null;
    mode = "source";
    link = { url: "", title: "", publisher: "", kind: "" };
    upload = null;
    quote = "";
    summary = "";
    submitted = null;
    failed = null;
    data = null;
    keep({ edit: null });
  };

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    const [name] = changed;
    if (!ready || name == null) {
      return;
    }
    submitting = true;
    failed = null;
    try {
      const response = await fetch("/proposals/new", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          entityId: entity,
          nameId: name.id,
          text: drafts[name.id],
          evidence:
            shown === "source"
              ? { type: "source", sourceId: source }
              : shown === "url"
                ? { type: "url", ...link }
                : {
                    type: "upload",
                    uploadId: upload?.id,
                    page: upload?.page ?? null,
                    region: upload?.region ?? null,
                    title: link.title,
                    publisher: link.publisher,
                    kind: link.kind,
                  },
          quote,
          summary,
        }),
      });
      const body = (await response.json()) as {
        number?: number;
        message?: string;
      };
      if (!response.ok || body.number == null) {
        failed = m.edit_failed({ reason: body.message ?? response.statusText });
        return;
      }
      submitted = body.number;
    } finally {
      submitting = false;
    }
  };

  const typing = (target: EventTarget | null) =>
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

  const keydown = (event: KeyboardEvent) => {
    if (
      event.key === "e" &&
      !open &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      !typing(event.target)
    ) {
      event.preventDefault();
      keep({ edit: "" });
    } else if (event.key === "Escape" && open && !typing(event.target)) {
      close();
    }
  };
</script>

<svelte:window onkeydown={keydown} />

<button
  type="button"
  class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
  aria-expanded={open}
  aria-keyshortcuts="e"
  onclick={() => keep({ edit: "" })}
  ><PencilIcon class="block-3.5 inline-3.5" />{m.edit_action()}</button
>

{#if open}
  <aside
    class={cn(
      "contrib-sheet fixed z-40 flex flex-col bg-background",
      "max-lg:inset-x-0 max-lg:inset-be-0 max-lg:rounded-t-md max-lg:border-bs max-lg:shadow-[0_-8px_24px_-12px_rgb(0_0_0/0.25)]",
      "lg:inset-y-0 lg:inset-e-0 lg:border-s lg:inline-105",
      {
        "max-lg:block-17": sheet === "peek",
        "max-lg:block-[58dvh]": sheet === "half",
        "max-lg:block-[92dvh]": sheet === "full",
      },
    )}
    aria-labelledby="edit-panel-title"
  >
    <button
      type="button"
      class="grid place-items-center pbs-2 lg:hidden"
      aria-label={m.edit_resize()}
      onclick={() =>
        (sheet =
          sheet === "half" ? "full" : sheet === "full" ? "peek" : "half")}
    >
      <span class="rounded-full bg-input block-1 inline-10"></span>
    </button>

    <div class="flex items-start justify-between gap-3 border-be px-4 py-3">
      <div class="min-inline-0">
        <h2 id="edit-panel-title" class="text-base font-bold">{title}</h2>
        <p class="truncate text-sm text-muted-foreground">{label}</p>
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label={m.edit_close()}
        onclick={close}><X /></Button
      >
    </div>

    {#if submitted != null}
      <div
        class="contrib-settle grid flex-1 content-start gap-4 overflow-y-auto px-4 py-6"
      >
        <p class="heading">{m.edit_submitted({ number: submitted })}</p>
        <p class="text-sm">{m.edit_submitted_body()}</p>
        <div>
          <Button
            href={resolve("/proposals/[number]", { number: String(submitted) })}
            >{m.edit_view()}</Button
          >
        </div>
      </div>
    {:else if data == null}
      <p class="flex-1 px-4 py-6 text-sm text-muted-foreground" role="status">
        {failed ?? m.edit_loading()}
      </p>
    {:else}
      <form class="flex flex-1 flex-col min-block-0" onsubmit={submit}>
        <div class="flex-1 overflow-y-auto overscroll-contain">
          {#if data.open.length > 0}
            <section
              class="border-be px-4 py-2.5 text-sm"
              aria-labelledby="edit-open"
            >
              <h3 id="edit-open" class="text-muted-foreground">
                {m.edit_open()}
              </h3>
              <ul class="mbs-1.5 grid gap-1">
                {#each data.open as item (item.number)}
                  <li>
                    <a
                      class="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-2 rounded-sm p-1 hover:bg-muted"
                      href={resolve("/proposals/[number]", {
                        number: String(item.number),
                      })}
                    >
                      <span class="font-figures text-muted-foreground"
                        >#{item.number}</span
                      >
                      <span class="truncate">{item.title}</span>
                      <span class="text-xs text-muted-foreground"
                        >{stateLabel[item.state]()}</span
                      >
                    </a>
                  </li>
                {/each}
              </ul>
            </section>
          {/if}

          <section class="border-be px-4 py-3" aria-labelledby="edit-names">
            <h3 id="edit-names" class="text-sm font-bold">{m.edit_names()}</h3>
            <dl
              class="mbs-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 text-sm"
            >
              {#each data.names as name (name.id)}
                {const draft = $derived(drafts[name.id] ?? name.text)}
                {const dirty = $derived(draft.trim() !== name.text)}
                <dt class="pbs-2 text-muted-foreground">
                  {kindLabel[name.kind]()}{#if name.shown}<span
                      class="block text-xs">{m.edit_shown()}</span
                    >{:else if name.is_preferred}<span class="block text-xs"
                      >{m.edit_preferred()}</span
                    >{/if}
                </dt>
                <dd class="py-1">
                  <Input
                    value={draft}
                    lang={name.lang ?? undefined}
                    dir="auto"
                    readonly={!signedIn}
                    disabled={signedIn &&
                      changed.some((other) => other.id !== name.id)}
                    aria-label={kindLabel[name.kind]()}
                    class="border-transparent bg-transparent px-1.5 block-8 hover:border-input"
                    oninput={(
                      event: Event & { currentTarget: HTMLInputElement },
                    ) => (drafts[name.id] = event.currentTarget.value)}
                  />
                  {#if dirty}
                    <Proof
                      class="contrib-settle ps-1.5 text-xs"
                      before={name.text}
                      after={draft.trim()}
                      n={1}
                    />
                    <div class="mbs-2 grid gap-2 ps-1.5">
                      <ToggleGroup.Root
                        type="single"
                        variant="outline"
                        size="sm"
                        class="grid auto-cols-fr grid-flow-col inline-full"
                        aria-label={m.edit_evidence()}
                        value={shown}
                        onValueChange={(next: string) => {
                          if (
                            next === "source" ||
                            next === "url" ||
                            next === "upload"
                          ) {
                            mode = next;
                          }
                        }}
                      >
                        {#each modes as option (option)}
                          <ToggleGroup.Item
                            value={option}
                            class="px-1.5 text-xs text-muted-foreground data-[state=on]:font-semibold data-[state=on]:text-foreground"
                            >{modeLabel[option]()}</ToggleGroup.Item
                          >
                        {/each}
                      </ToggleGroup.Root>
                      {#if shown === "source"}
                        <Combobox
                          items={sources}
                          bind:value={source}
                          label={m.edit_evidence()}
                          placeholder={m.edit_evidence_placeholder()}
                          search={m.edit_evidence_search()}
                          empty={m.edit_evidence_empty()}
                        />
                      {:else}
                        <fieldset class="contrib-settle grid gap-2">
                          <legend class="sr-only">{m.edit_evidence()}</legend>
                          {#if shown === "url"}
                            <Input
                              type="url"
                              inputmode="url"
                              bind:value={link.url}
                              placeholder="https://"
                              aria-label={m.edit_url()}
                              maxlength={2048}
                              class="block-8"
                            />
                          {:else}
                            <UploadEvidence bind:value={upload} />
                          {/if}
                          <Input
                            bind:value={link.title}
                            placeholder={m.edit_url_title_placeholder()}
                            aria-label={m.edit_url_title()}
                            maxlength={500}
                            class="block-8"
                          />
                          <div
                            class="grid grid-cols-[minmax(0,1fr)_auto] gap-2"
                          >
                            <Input
                              bind:value={link.publisher}
                              placeholder={m.edit_url_publisher_placeholder()}
                              aria-label={m.edit_url_publisher()}
                              maxlength={200}
                              class="block-8"
                            />
                            <Select.Root type="single" bind:value={link.kind}>
                              <Select.Trigger
                                class="text-sm block-8"
                                aria-label={m.edit_url_kind()}
                                >{chosen == null
                                  ? m.edit_url_kind()
                                  : sourceKindLabel[chosen]()}</Select.Trigger
                              >
                              <Select.Content align="end">
                                {#each sourceKinds as kind (kind)}
                                  <Select.Item value={kind}
                                    >{sourceKindLabel[kind]()}</Select.Item
                                  >
                                {/each}
                              </Select.Content>
                            </Select.Root>
                          </div>
                          {#if shown === "url"}
                            <p
                              class="text-xs text-pretty text-muted-foreground"
                            >
                              {m.edit_url_note()}
                            </p>
                          {/if}
                        </fieldset>
                      {/if}
                      <Input
                        bind:value={quote}
                        placeholder={m.edit_quote_placeholder()}
                        aria-label={m.edit_quote()}
                        class="block-8"
                      />
                    </div>
                  {:else}
                    <span class="block ps-1.5 text-xs text-muted-foreground"
                      >{name.source ?? m.edit_no_source()}</span
                    >
                  {/if}
                </dd>
              {/each}
            </dl>
          </section>
        </div>

        <footer class="grid gap-2 border-bs px-4 py-3">
          {#if signedIn}
            <Input
              bind:value={summary}
              placeholder={m.edit_summary_placeholder()}
              aria-label={m.edit_summary()}
              maxlength={200}
            />
            <p class="text-sm text-muted-foreground" role="status">
              {#if failed != null}
                <span class="text-destructive">{failed}</span>
              {:else if changed.length > 1}
                {m.edit_one_at_a_time()}
              {:else if changed.length === 0}
                {m.edit_no_change()}
              {:else if !cited}
                {shown === "source"
                  ? m.edit_needs_source()
                  : shown === "url"
                    ? m.edit_needs_link()
                    : m.edit_needs_upload()}
              {:else}
                {m.edit_one_change()}
              {/if}
            </p>
            <div class="flex justify-end">
              <Button type="submit" disabled={!ready || submitting}
                >{submitting ? m.edit_submitting() : m.edit_submit()}</Button
              >
            </div>
          {:else}
            <p class="text-sm text-muted-foreground">{m.edit_signed_out()}</p>
            <div>
              <Button
                href="{resolve('/sign-in')}?next={encodeURIComponent(
                  page.url.pathname + page.url.search,
                )}">{m.edit_sign_in()}</Button
              >
            </div>
          {/if}
        </footer>
      </form>
    {/if}
  </aside>
{/if}
