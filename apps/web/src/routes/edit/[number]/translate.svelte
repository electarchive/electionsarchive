<script lang="ts">
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import type { Origin } from "#lib/contrib.js";
  import type { Noted } from "#lib/server/workspace.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import {
    graphemes,
    originLabel,
    origins,
    translationLimit,
  } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import {
    languageItems,
    languageLabel,
    templateTitle,
  } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import Cite from "./cite.svelte";
  import { none, Records } from "./records.svelte.js";
  import Ref from "./ref.svelte";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "translate" }>;
  } = $props();

  type Entry = { key: number; ref: string };

  let next = 0;
  const keyed = (ref = ""): Entry => ({ key: next++, ref });

  const initial = untrack(() => ({ ...data, workspace }));
  let lang = $state<string | null>(initial.workspace.lang);
  let origin = $state<Origin>(initial.workspace.origin);
  let entries = $state<Array<Entry>>(
    initial.workspace.records.length === 0
      ? [keyed()]
      : initial.workspace.records.map((ref) => keyed(ref)),
  );
  let bodies = $state<Record<string, string>>(
    Object.fromEntries(
      initial.workspace.bodies.map((item) => [item.note, item.body]),
    ),
  );
  let summary = $state(initial.summary);
  let cited = $state(initial.workspace.evidence);
  let loaded = $state<Record<string, Array<Noted>>>({});
  let checkRows = $state<Array<string>>([]);
  let selected = $state<number | null>(null);
  const records = new Records(initial.records, () =>
    entries.map((entry) => entry.ref),
  );
  const languages = languageItems();

  const looked = $derived(entries.map((entry) => records.look(entry.ref)));
  const ids = $derived([
    ...new Set(looked.flatMap((item) => item.entity?.id ?? [])),
  ]);

  $effect(() => {
    const missing = ids.filter((id) => untrack(() => loaded[id]) == null);
    if (missing.length === 0) {
      return;
    }
    const controller = new AbortController();
    void (async () => {
      const response = await fetch("/edit/notes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ entities: missing }),
        signal: controller.signal,
      });
      if (!response.ok) {
        return;
      }
      const rows = (await response.json()) as Array<Noted>;
      if (controller.signal.aborted) {
        return;
      }
      const fetched: Record<string, Array<Noted>> = {};
      for (const id of missing) {
        fetched[id] = rows.filter((row) => row.entity === id);
      }
      loaded = { ...loaded, ...fetched };
    })().catch(() => undefined);
    return () => {
      controller.abort();
    };
  });

  const assessed = $derived.by(() => {
    const seenRecords: Record<string, boolean> = {};
    const seenTexts: Record<string, boolean> = {};
    return entries.map((entry, index) => {
      const look = looked[index] ?? none;
      const id = look.entity?.id ?? null;
      const isTwice = id != null && seenRecords[id] === true;
      if (id != null) {
        seenRecords[id] = true;
      }
      const notes = id == null || isTwice ? [] : (loaded[id] ?? null);
      const items = (notes ?? []).map((note, position) => {
        const isSame = lang != null && note.lang === lang;
        const isShared = !isSame && seenTexts[note.md5] === true;
        if (!isSame) {
          seenTexts[note.md5] = true;
        }
        const held =
          lang == null
            ? null
            : (note.translations.find((item) => item.lang === lang) ?? null);
        const body = (bodies[note.id] ?? "").trim();
        const limit = translationLimit(note.body_md);
        const isOpen = !isSame && !isShared;
        const problem =
          body === "" || !isOpen
            ? null
            : lang == null
              ? m.edit_translation_lang_missing()
              : origin === "machine" &&
                  held != null &&
                  held.origin !== "machine"
                ? m.edit_translation_machine_held()
                : graphemes(body) > limit
                  ? m.edit_translation_too_long({ limit: count(limit) })
                  : null;
        return {
          note,
          n: position + 1,
          isSame,
          isShared,
          isOpen,
          held,
          body,
          problem,
          isReady: isOpen && body !== "" && problem == null,
        };
      });
      return {
        entry,
        n: index + 1,
        name: look.entity?.name ?? entry.ref.trim(),
        look: {
          ...look,
          problem: look.problem ?? (isTwice ? m.ws_translate_twice() : null),
        },
        isLoading: notes == null,
        isEmpty: notes?.length === 0 && id != null && !isTwice,
        items,
      };
    });
  });

  const source = $derived.by(() => {
    if (cited == null || origin !== "official") {
      return null;
    }
    const url = cited.url.trim();
    const title = cited.title.trim();
    const publisher = cited.publisher.trim();
    return /^https?:\/\//iu.test(url) &&
      URL.canParse(url) &&
      title !== "" &&
      publisher !== ""
      ? { url, title, publisher, kind: cited.kind }
      : null;
  });

  const ready = $derived(
    assessed.flatMap((record) => record.items.filter((item) => item.isReady)),
  );

  const ops = $derived(
    lang == null
      ? []
      : ready.map((item) => ({
          op: "translate" as const,
          of: { table: "note" as const, id: item.note.id },
          lang,
          origin,
          body: item.body,
          ...(origin === "official" && { evidence: ["e1"] }),
        })),
  );

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "translate",
        lang,
        origin,
        records: entries
          .map((entry) => entry.ref)
          .filter((ref) => ref.trim() !== ""),
        bodies: Object.entries(bodies)
          .filter(([, body]) => body.trim() !== "")
          .map(([note, body]) => ({ note, body })),
        evidence: cited,
      },
    }),
  );

  const hasProblem = $derived(
    assessed.some(
      (record) =>
        record.look.problem != null ||
        record.items.some((item) => item.problem != null),
    ),
  );
  const isLookingUp = $derived(
    assessed.some((record) => record.look.isLooking || record.isLoading),
  );
  const needsSource = $derived(origin === "official" && source == null);

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () =>
        ops.length > 0 && !hasProblem && !isLookingUp && !needsSource,
      checked: () => {
        checkRows = ready.map((item) => item.note.id);
      },
    },
  );

  checkRows = untrack(() => ready.map((item) => item.note.id));

  const check = $derived(session.check);
  const isLocked = $derived(session.isLocked);

  const failure = $derived.by(() => {
    const error = session.error;
    if (error == null) {
      return null;
    }
    const note = error.op == null ? null : (checkRows[error.op] ?? null);
    const record = assessed.find((item) =>
      item.items.some((entry) => entry.note.id === note),
    );
    const item = record?.items.find((entry) => entry.note.id === note);
    return {
      note,
      label:
        record == null || item == null
          ? null
          : m.ws_translate_note({ record: record.name, n: item.n }),
      text: error.text,
    };
  });

  const blocker = $derived(
    session.blocker(
      hasProblem || failure != null
        ? m.ws_submit_needs_fix()
        : lang == null
          ? m.ws_translate_pick_lang()
          : ready.length === 0
            ? m.ws_submit_needs_translation()
            : needsSource
              ? m.ws_translate_evidence_official()
              : null,
      summary,
    ),
  );

  const translations = $derived(check?.checks?.diff?.translation?.insert ?? 0);

  async function focusNote(id: string) {
    await tick();
    const field = document.getElementById(`ws-tr-${id}`);
    field?.scrollIntoView({ block: "center" });
    field?.focus();
  }

  async function addRecord() {
    const entry = keyed();
    entries.push(entry);
    selected = entry.key;
    await tick();
    document.getElementById(`ws-tr-record-${entry.key}`)?.focus();
  }

  function removeRecord(key: number) {
    const record = assessed.find((item) => item.entry.key === key);
    const dropped = (record?.items ?? []).map((item) => item.note.id);
    bodies = Object.fromEntries(
      Object.entries(bodies).filter(([note]) => !dropped.includes(note)),
    );
    entries = entries.filter((entry) => entry.key !== key);
  }
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle.translate()}
  bind:summary
  {blocker}
  failure={failure == null
    ? null
    : {
        label: failure.label,
        text: failure.text,
        pick:
          failure.note == null
            ? null
            : () => void focusNote(failure.note ?? ""),
      }}
>
  {#snippet outline()}
    <div class="grid gap-2 px-2 pbe-3">
      <p class="px-2 font-semibold">
        {lang == null ? templateTitle.translate() : languageLabel(lang)}
      </p>
      <ul class="grid">
        {#each assessed as record (record.entry.key)}
          {#if record.name !== ""}
            {const open = $derived(record.items.filter((item) => item.isOpen))}
            <li>
              <button
                type="button"
                class={cn(
                  "grid grid-cols-[minmax(0,1fr)] gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
                  { "bg-muted": selected === record.entry.key },
                )}
                onclick={() => {
                  selected = record.entry.key;
                  document
                    .getElementById(`ws-tr-record-${record.entry.key}`)
                    ?.scrollIntoView({ block: "start" });
                }}
              >
                <span
                  class="flex items-baseline justify-between gap-2 min-inline-0"
                >
                  <span class="truncate font-semibold min-inline-0"
                    >{record.name}</span
                  >
                  {#if record.look.problem != null || record.items.some((item) => item.problem != null)}
                    <span class="font-figures font-semibold text-destructive"
                      >{record.n}</span
                    >
                  {/if}
                </span>
                {#if open.length > 0}
                  <span class="font-figures text-xs text-muted-foreground"
                    >{m.ws_outline_translated({
                      count: open.filter((item) => item.isReady).length,
                      total: open.length,
                    })}</span
                  >
                {/if}
              </button>
            </li>
          {/if}
        {/each}
      </ul>
    </div>
  {/snippet}

  {#snippet editor()}
    <section class="grid gap-x-4 gap-y-3 p-4 sm:grid-cols-2">
      <div class="grid content-start gap-1">
        <span class="text-xs text-muted-foreground" aria-hidden="true"
          >{m.edit_translation_lang()}</span
        >
        {#if isLocked}
          <p class="text-sm/8">
            {lang == null ? "" : languageLabel(lang)}
          </p>
        {:else}
          <Combobox
            items={languages}
            value={lang}
            label={m.edit_translation_lang()}
            placeholder={m.ws_translate_pick_lang()}
            search={m.edit_note_lang_search()}
            empty={m.edit_note_lang_empty()}
            class="block-8 inline-full"
            onValueChange={(chosen: string | null) => {
              lang = chosen;
            }}
          />
        {/if}
      </div>
      <div class="grid content-start gap-1">
        <span id="ws-tr-origin-label" class="text-xs text-muted-foreground"
          >{m.edit_translation_origin()}</span
        >
        <Select.Root
          type="single"
          value={origin}
          disabled={isLocked}
          onValueChange={(chosen: string) => {
            origin = origins.find((item) => item === chosen) ?? "human";
          }}
        >
          <Select.Trigger
            id="ws-tr-origin"
            class="text-sm block-8 inline-full"
            aria-labelledby="ws-tr-origin-label"
            >{originLabel[origin]()}</Select.Trigger
          >
          <Select.Content>
            {#each origins as item (item)}
              <Select.Item value={item}>{originLabel[item]()}</Select.Item>
            {/each}
          </Select.Content>
        </Select.Root>
        {#if origin === "machine"}
          <p class="text-xs text-pretty text-muted-foreground">
            {m.edit_translation_machine()}
          </p>
        {:else if origin === "official"}
          <p class="text-xs text-pretty text-muted-foreground">
            {m.ws_translate_evidence_official()}
          </p>
        {/if}
      </div>
    </section>

    <section class="grid gap-3 border-bs p-4" aria-labelledby="ws-tr-records">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="ws-tr-records" class="font-semibold">
          {m.ws_translate_records()}
        </h2>
        <span class="text-xs text-pretty text-muted-foreground"
          >{m.ws_translate_records_hint()}</span
        >
      </div>
      <ol class="grid border-bs">
        {#each assessed as record (record.entry.key)}
          {const entry = $derived(record.entry)}
          <li
            class={cn(
              "grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2 border-s-2 border-be py-3",
              {
                "border-s-destructive":
                  record.look.problem != null ||
                  record.items.some(
                    (item) =>
                      item.problem != null || failure?.note === item.note.id,
                  ),
                "border-s-transparent":
                  record.look.problem == null &&
                  record.items.every(
                    (item) =>
                      item.problem == null && failure?.note !== item.note.id,
                  ),
              },
            )}
          >
            <span
              class="pbs-6 text-end font-figures text-xs text-muted-foreground"
              aria-hidden="true">{record.n}</span
            >
            <div class="grid gap-3 min-inline-0">
              <div class="grid grid-cols-[minmax(0,1fr)_2rem] gap-x-2">
                <div class="sm:inline-1/2">
                  <Ref
                    bind:value={entry.ref}
                    id="ws-tr-record-{entry.key}"
                    label={m.ws_translate_record({ row: record.n })}
                    {isLocked}
                    state={record.look}
                    onfocus={() => (selected = entry.key)}
                  />
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  class="mbs-5 text-muted-foreground block-8 inline-8"
                  disabled={isLocked}
                  aria-label={m.ws_translate_remove_record({ row: record.n })}
                  onclick={() => removeRecord(entry.key)}><XIcon /></Button
                >
              </div>
              {#if record.isLoading && record.look.entity != null}
                <p class="text-xs text-muted-foreground">
                  {m.ws_record_looking()}
                </p>
              {:else if record.isEmpty}
                <p class="text-sm text-pretty text-muted-foreground">
                  {m.ws_translate_no_notes()}
                </p>
              {/if}
              {#each record.items as item (item.note.id)}
                {const note = $derived(item.note)}
                {const label = $derived(
                  m.ws_translate_note({ record: record.name, n: item.n }),
                )}
                {const isFailed = $derived(failure?.note === note.id)}
                <div
                  class="grid gap-x-4 gap-y-2 border-bs pbs-3 sm:grid-cols-2"
                >
                  <div class="grid content-start gap-1 min-inline-0">
                    <span class="text-xs text-muted-foreground"
                      >{item.n}. {m.ws_translate_original({
                        language: languageLabel(note.lang),
                      })}</span
                    >
                    <p
                      lang={note.lang}
                      dir="auto"
                      class="overflow-auto text-sm text-pretty whitespace-pre-wrap max-block-64"
                    >
                      {note.body_md}
                    </p>
                  </div>
                  <div class="grid content-start gap-1 min-inline-0">
                    {#if item.isSame}
                      <p class="text-sm text-pretty text-muted-foreground">
                        {m.ws_translate_same_lang({
                          language: languageLabel(note.lang),
                        })}
                      </p>
                    {:else if item.isShared}
                      <p class="text-sm text-pretty text-muted-foreground">
                        {m.ws_translate_shared()}
                      </p>
                    {:else}
                      {#if item.held != null}
                        <span class="text-xs text-muted-foreground"
                          >{m.ws_translate_current({
                            origin: originLabel[item.held.origin](),
                          })}{#if item.held.stale}, {m.edit_translation_outdated()}{/if}</span
                        >
                        <p
                          lang={lang ?? undefined}
                          dir="auto"
                          class="line-clamp-3 text-sm text-pretty text-muted-foreground"
                        >
                          {item.held.body}
                        </p>
                      {/if}
                      <label for="ws-tr-{note.id}" class="sr-only"
                        >{label}</label
                      >
                      <Textarea
                        id="ws-tr-{note.id}"
                        bind:value={
                          () => bodies[note.id] ?? "",
                          (value: string) => (bodies[note.id] = value)
                        }
                        lang={lang ?? undefined}
                        dir="auto"
                        rows={4}
                        disabled={isLocked}
                        placeholder={m.edit_translation_placeholder()}
                        aria-invalid={item.problem != null || isFailed}
                        class="text-sm"
                        onfocus={() => (selected = entry.key)}
                      />
                      {#if item.problem != null}
                        <p
                          class="text-xs font-semibold text-pretty text-destructive"
                        >
                          {item.problem}
                        </p>
                      {:else if isFailed}
                        <p
                          class="text-xs font-semibold text-pretty text-destructive"
                        >
                          {failure?.text}
                        </p>
                      {:else if item.held != null && item.body !== ""}
                        <p class="text-xs text-muted-foreground">
                          {item.held.stale
                            ? m.edit_translation_replaces_outdated()
                            : m.edit_translation_replaces()}
                        </p>
                      {/if}
                    {/if}
                  </div>
                </div>
              {/each}
            </div>
          </li>
        {/each}
      </ol>
      {#if !isLocked}
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          onclick={() => void addRecord()}
          ><PlusIcon />{m.ws_translate_add_record()}</Button
        >
      {/if}
    </section>
  {/snippet}

  {#snippet diff()}
    {#if translations === 0}
      <p class="text-pretty text-muted-foreground">{m.ws_diff_empty()}</p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <dl class="grid border-s-2 border-foreground">
        <div class="flex items-baseline justify-between border-be py-1.5 ps-3">
          <dt>{m.ws_diff_translations()}</dt>
          <dd class="font-figures font-semibold">{translations}</dd>
        </div>
      </dl>
    {/if}
  {/snippet}

  {#snippet evidence()}
    {#if origin === "official"}
      <Cite bind:cited {isLocked} empty={m.ws_translate_evidence_official()} />
    {:else}
      <p class="text-pretty text-muted-foreground">
        {m.ws_translate_evidence_none()}
      </p>
    {/if}
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item
      disabled={isLocked}
      onSelect={() => act(() => void addRecord())}
      >{m.ws_translate_add_record()}</Command.Item
    >
  {/snippet}
</Shell>
