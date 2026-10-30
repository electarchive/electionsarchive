<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import { SvelteSet } from "svelte/reactivity";
  import type { Change, Editable, Scheme } from "#lib/contrib.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import UploadEvidence from "#lib/components/contrib/upload-evidence.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import * as ToggleGroup from "#lib/components/ui/toggle-group/index.js";
  import {
    graphemes,
    kindLabel,
    languages,
    noteKindLabel,
    noteKinds,
    noteLimit,
    personSchemes,
    schemeExample,
    schemeHref,
    schemeLabel,
    schemePattern,
    sourceKindLabel,
    sourceKinds,
    stateLabel,
  } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";

  const {
    entity,
    label,
    title,
  }: { entity: string; label: string; title: string } = $props();

  const open = $derived(page.url.searchParams.has("edit"));
  const signedIn = $derived(page.data.user != null);
  const locale = getLocale();
  const languageName = new Intl.DisplayNames([locale], { type: "language" });
  const languageItems = languages.map((tag) => ({
    value: tag,
    label: languageName.of(tag) ?? tag,
    detail: tag,
  }));
  const defaultLang =
    languages.find((tag) => tag === locale) ??
    languages.find((tag) => tag === locale.split("-")[0]) ??
    "en";

  let data = $state<Editable | null>(null);
  let failed = $state<string | null>(null);
  let sheet = $state<"peek" | "half" | "full">("half");
  let drafts = $state<Record<string, string>>({});
  let noteDrafts = $state<Record<string, string>>({});
  const noteDrops = new SvelteSet<string>();
  let addedNotes = $state<
    Array<{
      key: number;
      noteKind: (typeof noteKinds)[number];
      lang: string;
      body: string;
    }>
  >([]);
  const idDrops = new SvelteSet<string>();
  let addedIds = $state<
    Array<{ key: number; scheme: Scheme | ""; value: string }>
  >([]);
  let added = 0;
  let source = $state<string | null>(null);
  let mode = $state<"none" | "source" | "url" | "upload">("none");
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

  const notes = $derived(data?.notes ?? []);
  const held = (scheme: Scheme) =>
    (data?.ids ?? []).some(
      (id) => id.scheme === scheme && !idDrops.has(`${id.scheme}:${id.value}`),
    );
  const idProblem = (
    item: (typeof addedIds)[number],
    index: number,
  ): string | null => {
    if (item.scheme === "" || item.value.trim() === "") {
      return null;
    }
    const scheme = item.scheme;
    if (!schemePattern[scheme].test(item.value.trim())) {
      return m.edit_id_invalid({ scheme: schemeLabel[scheme]() });
    }
    if (
      held(scheme) ||
      addedIds.some((other, at) => at < index && other.scheme === scheme)
    ) {
      return m.edit_id_taken({ scheme: schemeLabel[scheme]() });
    }
    return null;
  };
  const tooLong = (body: string) => graphemes(body) > noteLimit;

  const changes = $derived.by(() => {
    const list: Array<Change> = [];
    for (const name of data?.names ?? []) {
      const text = (drafts[name.id] ?? name.text).trim();
      if (text !== name.text && text !== "") {
        list.push({ kind: "name", id: name.id, text });
      }
    }
    for (const note of notes) {
      if (noteDrops.has(note.id)) {
        list.push({ kind: "note_drop", id: note.id });
        continue;
      }
      const body = (noteDrafts[note.id] ?? note.body_md).trim();
      if (body !== note.body_md.trim() && body !== "") {
        list.push({ kind: "note_set", id: note.id, body });
      }
    }
    for (const note of addedNotes) {
      if (note.body.trim() !== "") {
        list.push({
          kind: "note_add",
          noteKind: note.noteKind,
          lang: note.lang,
          body: note.body.trim(),
        });
      }
    }
    for (const id of data?.ids ?? []) {
      if (idDrops.has(`${id.scheme}:${id.value}`)) {
        list.push({ kind: "id_drop", scheme: id.scheme, value: id.value });
      }
    }
    for (const id of addedIds) {
      if (id.scheme !== "" && id.value.trim() !== "") {
        list.push({
          kind: "id_add",
          scheme: id.scheme,
          value: id.value.trim(),
        });
      }
    }
    return list;
  });
  const invalid = $derived(
    Object.values(drafts).some((text) => text.trim() === "") ||
      notes.some(
        (note) =>
          !noteDrops.has(note.id) &&
          ((noteDrafts[note.id] ?? note.body_md).trim() === "" ||
            tooLong(noteDrafts[note.id] ?? note.body_md)),
      ) ||
      addedNotes.some((note) => tooLong(note.body)) ||
      addedIds.some(
        (item, index) =>
          idProblem(item, index) != null ||
          (item.scheme === "") !== (item.value.trim() === ""),
      ),
  );
  const required = $derived(changes.some((change) => change.kind === "name"));
  const sources = $derived(
    (data?.sources ?? []).map((item) => ({
      value: item.id,
      label: item.title,
      detail: item.publisher ?? undefined,
    })),
  );
  const modes = $derived([
    ...(required ? [] : (["none"] as const)),
    ...(sources.length === 0 ? [] : (["source"] as const)),
    "url" as const,
    "upload" as const,
  ]);
  const shown = $derived(
    modes.find((option) => option === mode) ?? modes[0] ?? "url",
  );
  const modeLabel = {
    none: m.edit_mode_none,
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
    shown === "none"
      ? true
      : shown === "source"
        ? source != null
        : shown === "url"
          ? described && link.url.trim() !== ""
          : described && upload != null,
  );
  const ready = $derived(
    changes.length > 0 && !invalid && cited && summary.trim() !== "",
  );

  const close = () => {
    drafts = {};
    noteDrafts = {};
    noteDrops.clear();
    addedNotes = [];
    idDrops.clear();
    addedIds = [];
    source = null;
    mode = "none";
    link = { url: "", title: "", publisher: "", kind: "" };
    upload = null;
    quote = "";
    summary = "";
    submitted = null;
    failed = null;
    data = null;
    keep({ edit: null });
  };

  const evidence = () => {
    switch (shown) {
      case "none": {
        return null;
      }
      case "source": {
        return { type: "source", sourceId: source };
      }
      case "url": {
        return { type: "url", ...link };
      }
      case "upload": {
        return {
          type: "upload",
          uploadId: upload?.id,
          page: upload?.page ?? null,
          region: upload?.region ?? null,
          title: link.title,
          publisher: link.publisher,
          kind: link.kind,
        };
      }
    }
  };

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    if (!ready) {
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
          changes,
          evidence: evidence(),
          quote: shown === "none" ? "" : quote,
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
                    aria-label={kindLabel[name.kind]()}
                    aria-invalid={dirty && draft.trim() === ""}
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
                    />
                  {:else}
                    <span class="block ps-1.5 text-xs text-muted-foreground"
                      >{name.source ?? m.edit_no_source()}</span
                    >
                  {/if}
                </dd>
              {/each}
            </dl>
          </section>

          <section class="border-be px-4 py-3" aria-labelledby="edit-notes">
            <h3 id="edit-notes" class="text-sm font-bold">{m.edit_notes()}</h3>
            {#if notes.length === 0 && addedNotes.length === 0}
              <p class="mbs-1 text-sm text-muted-foreground">
                {m.edit_notes_empty()}
              </p>
            {/if}
            <dl
              class="mbs-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm"
            >
              {#each notes as note (note.id)}
                {const dropped = $derived(noteDrops.has(note.id))}
                {const body = $derived(noteDrafts[note.id] ?? note.body_md)}
                {const dirty = $derived(body.trim() !== note.body_md.trim())}
                <dt class="pbs-2 text-muted-foreground">
                  {noteKindLabel[note.kind]()}<span class="block text-xs"
                    >{languageName.of(note.lang) ?? note.lang}</span
                  >
                </dt>
                <dd class="grid gap-1 py-1">
                  {#if dropped}
                    <p
                      class="contrib-settle px-1.5 text-pretty whitespace-pre-line text-muted-foreground line-through decoration-foreground/50"
                      lang={note.lang}
                    >
                      {note.body_md}
                    </p>
                  {:else}
                    <Textarea
                      value={body}
                      lang={note.lang}
                      dir="auto"
                      readonly={!signedIn}
                      aria-label={noteKindLabel[note.kind]()}
                      aria-invalid={tooLong(body) || body.trim() === ""}
                      class="border-transparent bg-transparent px-1.5 py-1 shadow-none min-block-0 hover:border-input md:text-sm"
                      oninput={(
                        event: Event & { currentTarget: HTMLTextAreaElement },
                      ) => (noteDrafts[note.id] = event.currentTarget.value)}
                    />
                  {/if}
                  {#if signedIn}
                    <div
                      class="flex items-center justify-between gap-2 ps-1.5 text-xs text-muted-foreground"
                    >
                      <span>
                        {#if dropped}
                          <span class="font-semibold text-foreground"
                            >{m.edit_removed()}</span
                          >
                        {:else if tooLong(body)}
                          <span class="text-destructive"
                            >{m.edit_note_too_long({
                              limit: count(noteLimit),
                            })}</span
                          >
                        {:else if dirty}
                          {m.edit_note_count({
                            count: count(graphemes(body)),
                            limit: count(noteLimit),
                          })}
                        {/if}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        class="px-2 text-xs block-7"
                        onclick={() => {
                          if (dropped) {
                            noteDrops.delete(note.id);
                          } else {
                            noteDrops.add(note.id);
                          }
                        }}
                        >{dropped
                          ? m.edit_note_keep()
                          : m.edit_note_remove()}</Button
                      >
                    </div>
                  {/if}
                </dd>
              {/each}
              {#each addedNotes as note, index (note.key)}
                {const kindName = $derived(noteKindLabel[note.noteKind]())}
                <dt class="pbs-2 font-semibold text-foreground">
                  {m.edit_note_add()}
                </dt>
                <dd class="contrib-settle grid gap-2 py-1">
                  <div class="grid grid-cols-[auto_minmax(0,1fr)] gap-2">
                    <Select.Root
                      type="single"
                      value={note.noteKind}
                      onValueChange={(next: string) => {
                        const kind = noteKinds.find((item) => item === next);
                        if (kind != null) {
                          note.noteKind = kind;
                        }
                      }}
                    >
                      <Select.Trigger
                        class="text-sm block-8"
                        aria-label={m.edit_note_kind()}
                        >{kindName}</Select.Trigger
                      >
                      <Select.Content>
                        {#each noteKinds as kind (kind)}
                          <Select.Item value={kind}
                            >{noteKindLabel[kind]()}</Select.Item
                          >
                        {/each}
                      </Select.Content>
                    </Select.Root>
                    <Combobox
                      items={languageItems}
                      value={note.lang}
                      label={m.edit_note_lang()}
                      placeholder={m.edit_note_lang()}
                      search={m.edit_note_lang_search()}
                      empty={m.edit_note_lang_empty()}
                      onValueChange={(next: string | null) => {
                        if (next != null) {
                          note.lang = next;
                        }
                      }}
                    />
                  </div>
                  <Textarea
                    bind:value={note.body}
                    lang={note.lang}
                    dir="auto"
                    placeholder={m.edit_note_body_placeholder()}
                    aria-label={m.edit_note_body()}
                    aria-invalid={tooLong(note.body)}
                    class="min-block-20 md:text-sm"
                  />
                  <div
                    class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                  >
                    {#if tooLong(note.body)}
                      <span class="text-destructive"
                        >{m.edit_note_too_long({
                          limit: count(noteLimit),
                        })}</span
                      >
                    {:else}
                      <span
                        >{m.edit_note_count({
                          count: count(graphemes(note.body)),
                          limit: count(noteLimit),
                        })}</span
                      >
                    {/if}
                    <Button
                      variant="ghost"
                      size="sm"
                      class="px-2 text-xs block-7"
                      onclick={() => addedNotes.splice(index, 1)}
                      >{m.edit_note_discard()}</Button
                    >
                  </div>
                </dd>
              {/each}
            </dl>
            {#if signedIn}
              <Button
                variant="outline"
                size="sm"
                class="mbs-2"
                onclick={() => {
                  added += 1;
                  addedNotes.push({
                    key: added,
                    noteKind: "context",
                    lang: defaultLang,
                    body: "",
                  });
                }}><PlusIcon />{m.edit_note_add()}</Button
              >
            {/if}
          </section>

          <section class="border-be px-4 py-3" aria-labelledby="edit-ids">
            <h3 id="edit-ids" class="text-sm font-bold">{m.edit_ids()}</h3>
            {#if data.ids.length === 0 && addedIds.length === 0}
              <p class="mbs-1 text-sm text-muted-foreground">
                {m.edit_ids_empty()}
              </p>
            {/if}
            <dl
              class="mbs-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm"
            >
              {#each data.ids as id (`${id.scheme}:${id.value}`)}
                {const dropped = $derived(
                  idDrops.has(`${id.scheme}:${id.value}`),
                )}
                {const href = $derived(schemeHref[id.scheme]?.(id.value))}
                <dt class="pbs-1.5 text-muted-foreground">
                  {schemeLabel[id.scheme]()}
                </dt>
                <dd class="flex items-center justify-between gap-2">
                  <span
                    class={cn("px-1.5 font-figures", {
                      "contrib-settle text-muted-foreground line-through decoration-foreground/50":
                        dropped,
                    })}
                  >
                    {#if href != null && !dropped}
                      <a
                        class="underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground"
                        {href}
                        rel="external noopener"
                        target="_blank">{id.value}</a
                      >
                    {:else}
                      {id.value}
                    {/if}
                  </span>
                  {#if signedIn}
                    <span class="flex items-center gap-2">
                      {#if dropped}
                        <span class="text-xs font-semibold"
                          >{m.edit_removed()}</span
                        >
                      {/if}
                      <Button
                        variant="ghost"
                        size="sm"
                        class="px-2 text-xs text-muted-foreground block-7"
                        onclick={() => {
                          const key = `${id.scheme}:${id.value}`;
                          if (dropped) {
                            idDrops.delete(key);
                          } else {
                            idDrops.add(key);
                          }
                        }}
                        >{dropped
                          ? m.edit_id_keep()
                          : m.edit_id_remove()}</Button
                      >
                    </span>
                  {/if}
                </dd>
              {/each}
              {#each addedIds as item, index (item.key)}
                {const problem = $derived(idProblem(item, index))}
                {const scheme = $derived(item.scheme)}
                <dt class="pbs-2 font-semibold text-foreground">
                  {m.edit_id_add()}
                </dt>
                <dd class="contrib-settle grid gap-1 py-1">
                  <div class="grid grid-cols-[auto_minmax(0,1fr)] gap-2">
                    <Select.Root
                      type="single"
                      value={item.scheme}
                      onValueChange={(next: string) => {
                        const found = personSchemes.find(
                          (option) => option === next,
                        );
                        if (found != null) {
                          item.scheme = found;
                        }
                      }}
                    >
                      <Select.Trigger
                        class="text-sm block-8"
                        aria-label={m.edit_id_scheme()}
                        >{scheme === ""
                          ? m.edit_id_scheme()
                          : schemeLabel[scheme]()}</Select.Trigger
                      >
                      <Select.Content>
                        {#each personSchemes as option (option)}
                          <Select.Item value={option}
                            >{schemeLabel[option]()}</Select.Item
                          >
                        {/each}
                      </Select.Content>
                    </Select.Root>
                    <Input
                      bind:value={item.value}
                      placeholder={scheme === "" ? "" : schemeExample[scheme]}
                      aria-label={m.edit_id_value()}
                      aria-invalid={problem != null}
                      autocomplete="off"
                      spellcheck={false}
                      maxlength={20}
                      class="font-figures block-8"
                    />
                  </div>
                  <div
                    class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                  >
                    <span class="text-destructive">{problem ?? ""}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      class="px-2 text-xs block-7"
                      onclick={() => addedIds.splice(index, 1)}
                      >{m.edit_id_discard()}</Button
                    >
                  </div>
                </dd>
              {/each}
            </dl>
            {#if signedIn}
              <Button
                variant="outline"
                size="sm"
                class="mbs-2"
                onclick={() => {
                  added += 1;
                  addedIds.push({ key: added, scheme: "", value: "" });
                }}><PlusIcon />{m.edit_id_add()}</Button
              >
            {/if}
          </section>

          {#if signedIn && changes.length > 0}
            <section
              class="contrib-settle grid gap-2 px-4 py-3"
              aria-labelledby="edit-evidence"
            >
              <h3 id="edit-evidence" class="text-sm font-bold">
                {m.edit_evidence()}
              </h3>
              <ToggleGroup.Root
                type="single"
                variant="outline"
                size="sm"
                class="grid auto-cols-fr grid-flow-col inline-full"
                aria-label={m.edit_evidence()}
                value={shown}
                onValueChange={(next: string) => {
                  const found = modes.find((option) => option === next);
                  if (found != null) {
                    mode = found;
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
              {#if shown === "none"}
                <p class="text-xs text-pretty text-muted-foreground">
                  {m.edit_evidence_optional()}
                </p>
              {:else}
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
                    <div class="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
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
                      <p class="text-xs text-pretty text-muted-foreground">
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
              {/if}
            </section>
          {/if}
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
              {:else if changes.length === 0}
                {m.edit_no_change()}
              {:else if invalid}
                {m.edit_needs_fix()}
              {:else if !cited}
                {shown === "source"
                  ? m.edit_needs_source()
                  : shown === "url"
                    ? m.edit_needs_link()
                    : m.edit_needs_upload()}
              {:else if summary.trim() === ""}
                {m.edit_needs_summary()}
              {:else}
                {m.edit_changes({ count: changes.length })}
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
