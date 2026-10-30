<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import LanguagesIcon from "@lucide/svelte/icons/languages";
  import PencilIcon from "@lucide/svelte/icons/pencil";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import X from "@lucide/svelte/icons/x";
  import { SvelteSet } from "svelte/reactivity";
  import type { Change, Editable, Origin, Scheme } from "#lib/contrib.js";
  import LabelsSection from "#lib/components/contrib/labels-section.svelte";
  import Proof from "#lib/components/contrib/proof.svelte";
  import ResultsGrid from "#lib/components/contrib/results-grid.svelte";
  import UploadEvidence from "#lib/components/contrib/upload-evidence.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import * as ToggleGroup from "#lib/components/ui/toggle-group/index.js";
  import {
    commonsName,
    graphemes,
    hex,
    isDay,
    isFree,
    kindLabel,
    languages,
    noteKindLabel,
    noteKinds,
    noteLimit,
    originLabel,
    origins,
    partySchemes,
    period,
    personSchemes,
    schemeExample,
    schemeHref,
    schemeLabel,
    schemePattern,
    sourceKindLabel,
    sourceKinds,
    stateLabel,
    translationLimit,
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
  let translations = $state<
    Array<{
      key: number;
      note: string;
      lang: string | null;
      origin: Origin;
      body: string;
    }>
  >([]);
  const idDrops = new SvelteSet<string>();
  let addedIds = $state<
    Array<{ key: number; scheme: Scheme | ""; value: string }>
  >([]);
  let colorDraft = $state<
    | { kind: "set"; from: string | null; color: string }
    | { kind: "end"; from: string | null; to: string }
    | null
  >(null);
  let newColor = $state<{ color: string; from: string; to: string } | null>(
    null,
  );
  let image = $state<{ file: string; from: string; to: string } | null>(null);
  let preview = $state<
    | { state: "idle" | "invalid" | "looking" | "missing" | "unreachable" }
    | {
        state: "found";
        name: string;
        thumb: string;
        license: string;
        artist: string;
        free: boolean;
      }
  >({ state: "idle" });
  let cite = $state(false);
  const uncites = new SvelteSet<string>();
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
  let figures = $state<Change | null>(null);
  let badFigures = $state(false);
  let labelChanges = $state<Array<Change>>([]);
  let badLabel = $state(false);

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

  const imageName = $derived(image == null ? null : commonsName(image.file));

  $effect(() => {
    const file = image?.file.trim() ?? "";
    const name = imageName;
    if (name == null) {
      preview = { state: file === "" ? "idle" : "invalid" };
      return;
    }
    preview = { state: "looking" };
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const query = new URLSearchParams({
        action: "query",
        format: "json",
        formatversion: "2",
        origin: "*",
        redirects: "1",
        prop: "imageinfo",
        iiprop: "url|extmetadata",
        iiurlwidth: "160",
        iiextmetadatafilter: "License|LicenseShortName|Artist|Credit",
        titles: `File:${name}`,
      });
      void fetch(`https://commons.wikimedia.org/w/api.php?${query}`, {
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error(response.statusText);
          }
          const body = (await response.json()) as {
            query?: {
              pages?: Array<{
                title: string;
                imageinfo?: Array<{
                  url: string;
                  thumburl?: string;
                  extmetadata?: Record<string, { value: unknown }>;
                }>;
              }>;
            };
          };
          const found = body.query?.pages?.[0];
          const info = found?.imageinfo?.[0];
          if (found == null || info == null) {
            preview = { state: "missing" };
            return;
          }
          const text = (key: string) =>
            new DOMParser()
              .parseFromString(
                String(info.extmetadata?.[key]?.value ?? ""),
                "text/html",
              )
              .body.textContent.trim();
          const license = text("LicenseShortName");
          preview = {
            state: "found",
            name: found.title.replace(/^File:/u, ""),
            thumb: info.thumburl ?? info.url,
            license,
            artist: text("Artist") || text("Credit"),
            free: isFree(text("License").toLowerCase(), license),
          };
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            preview = { state: "unreachable" };
          }
        });
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  });

  const imageProblem = $derived.by(() => {
    if (image == null) {
      return null;
    }
    switch (preview.state) {
      case "invalid": {
        return m.edit_image_invalid();
      }
      case "missing": {
        return m.edit_image_missing();
      }
      case "found": {
        if (!preview.free) {
          return m.edit_image_nonfree({
            license: preview.license || m.edit_image_no_license(),
          });
        }
        break;
      }
      case "idle":
      case "looking":
      case "unreachable": {
        break;
      }
    }
    const from = image.from.trim();
    const to = image.to.trim();
    if ((from !== "" && !isDay(from)) || (to !== "" && !isDay(to))) {
      return m.edit_date_invalid();
    }
    return from !== "" && to !== "" && from >= to ? m.edit_color_order() : null;
  });

  const notes = $derived(data?.notes ?? []);
  const entitySchemes = $derived(
    data?.kind === "party" ? partySchemes : personSchemes,
  );
  const colors = $derived(data?.colors ?? []);
  const colorEdit = $derived(
    colorDraft ??
      (newColor == null ? null : { kind: "add" as const, ...newColor }),
  );
  const colorProblem = $derived.by(() => {
    if (colorEdit == null) {
      return null;
    }
    if (colorEdit.kind === "set") {
      return hex.test(colorEdit.color.trim()) ? null : m.edit_color_invalid();
    }
    if (
      colorEdit.kind === "add" &&
      colorEdit.color.trim() !== "" &&
      !hex.test(colorEdit.color.trim())
    ) {
      return m.edit_color_invalid();
    }
    const from =
      colorEdit.kind === "add" ? colorEdit.from.trim() : (colorEdit.from ?? "");
    const to = colorEdit.to.trim();
    if (
      (colorEdit.kind === "add" && from !== "" && !isDay(from)) ||
      (to !== "" && !isDay(to))
    ) {
      return m.edit_date_invalid();
    }
    return from !== "" && to !== "" && from >= to ? m.edit_color_order() : null;
  });
  const colorChange = $derived.by((): Change | null => {
    if (colorEdit == null || colorProblem != null) {
      return null;
    }
    switch (colorEdit.kind) {
      case "set": {
        return {
          kind: "color_set",
          from: colorEdit.from,
          color: colorEdit.color.trim().toLowerCase(),
        };
      }
      case "end": {
        return colorEdit.to.trim() === ""
          ? null
          : {
              kind: "color_end",
              from: colorEdit.from,
              to: colorEdit.to.trim(),
            };
      }
      case "add": {
        return colorEdit.color.trim() === ""
          ? null
          : {
              kind: "color_add",
              color: colorEdit.color.trim().toLowerCase(),
              from: colorEdit.from.trim() === "" ? null : colorEdit.from.trim(),
              to: colorEdit.to.trim() === "" ? null : colorEdit.to.trim(),
            };
      }
    }
  });
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
  const locked = (note: Editable["notes"][number]) =>
    noteDrops.has(note.id) ||
    (noteDrafts[note.id] ?? note.body_md).trim() !== note.body_md.trim();
  const translationProblem = (
    draft: (typeof translations)[number],
  ): string | null => {
    const note = notes.find((item) => item.id === draft.note);
    if (note == null) {
      return null;
    }
    if (locked(note)) {
      return m.edit_translation_locked();
    }
    const held = note.translations.find((item) => item.lang === draft.lang);
    if (
      draft.origin === "machine" &&
      held != null &&
      held.origin !== "machine"
    ) {
      return m.edit_translation_machine_held();
    }
    if (graphemes(draft.body) > translationLimit(note.body_md)) {
      return m.edit_translation_too_long({
        limit: count(translationLimit(note.body_md)),
      });
    }
    if (draft.lang == null && draft.body.trim() !== "") {
      return m.edit_translation_lang_missing();
    }
    return null;
  };
  const translateNote = (note: Editable["notes"][number]) => {
    const taken = new Set([
      note.lang,
      ...translations
        .filter((draft) => draft.note === note.id)
        .map((draft) => draft.lang),
    ]);
    added += 1;
    translations.push({
      key: added,
      note: note.id,
      lang: taken.has(defaultLang) ? null : defaultLang,
      origin: "human",
      body: "",
    });
  };

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
    for (const draft of translations) {
      const note = notes.find((item) => item.id === draft.note);
      const body = draft.body.trim();
      const held = note?.translations.find((item) => item.lang === draft.lang);
      if (
        note != null &&
        draft.lang != null &&
        body !== "" &&
        !(
          held != null &&
          !held.stale &&
          held.body === body &&
          held.origin === draft.origin
        )
      ) {
        list.push({
          kind: "translate",
          note: note.id,
          md5: note.md5,
          lang: draft.lang,
          origin: draft.origin,
          body,
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
    if (colorChange != null) {
      list.push(colorChange);
    }
    if (cite) {
      list.push({ kind: "cite" });
    }
    for (const citation of data?.citations ?? []) {
      if (uncites.has(citation.id)) {
        for (const id of citation.own) {
          list.push({ kind: "cite_drop", id });
        }
      }
    }
    if (image != null && imageName != null) {
      list.push({
        kind: "image",
        file: imageName,
        from: image.from.trim() === "" ? null : image.from.trim(),
        to: image.to.trim() === "" ? null : image.to.trim(),
      });
    }
    if (figures != null) {
      list.push(figures);
    }
    list.push(...labelChanges);
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
      translations.some((draft) => translationProblem(draft) != null) ||
      addedIds.some(
        (item, index) =>
          idProblem(item, index) != null ||
          (item.scheme === "") !== (item.value.trim() === ""),
      ) ||
      colorProblem != null ||
      imageProblem != null ||
      badFigures ||
      badLabel,
  );
  const dated = $derived(
    image != null && (image.from.trim() !== "" || image.to.trim() !== ""),
  );
  const required = $derived(
    changes.some(
      (change) =>
        (change.kind === "image" && dated) ||
        change.kind === "name" ||
        change.kind === "cite" ||
        change.kind === "results" ||
        change.kind === "correct" ||
        change.kind === "label" ||
        change.kind.startsWith("color_") ||
        (change.kind === "translate" && change.origin === "official"),
    ),
  );
  const sources = $derived(
    (data?.sources ?? [])
      .filter(
        (item) =>
          !cite ||
          !(data?.citations ?? []).some((citation) => citation.id === item.id),
      )
      .map((item) => ({
        value: item.id,
        label: item.title,
        detail: item.publisher ?? undefined,
      })),
  );
  const modes = $derived([
    ...(required ? [] : (["none"] as const)),
    ...(sources.length === 0 ? [] : (["source"] as const)),
    "url" as const,
    ...(dated ? [] : (["upload"] as const)),
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
    translations = [];
    idDrops.clear();
    addedIds = [];
    colorDraft = null;
    newColor = null;
    image = null;
    figures = null;
    badFigures = false;
    labelChanges = [];
    badLabel = false;
    cite = false;
    uncites.clear();
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
      "lg:inset-y-0 lg:inset-e-0 lg:border-s",
      {
        "lg:inline-105": data?.results == null,
        "lg:inline-176": data?.results != null,
      },
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

          {#if data.results != null}
            <ResultsGrid
              results={data.results}
              {signedIn}
              bind:change={figures}
              bind:invalid={badFigures}
            />
          {/if}

          {#if data.kind != null}
            {const portrait = $derived(data.kind === "person")}
            <section class="border-be px-4 py-3" aria-labelledby="edit-images">
              <h3 id="edit-images" class="text-sm font-bold">
                {portrait ? m.edit_image_portrait() : m.edit_image_logo()}
              </h3>
              {#if data.images.length === 0 && image == null}
                <p class="mbs-1 text-sm text-muted-foreground">
                  {portrait
                    ? m.edit_image_empty_portrait()
                    : m.edit_image_empty_logo()}
                </p>
              {/if}
              {#if data.images.length > 0}
                <ul class="mbs-2 grid gap-2 text-sm">
                  {#each data.images as item (item.path)}
                    <li
                      class="grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-3"
                    >
                      <img
                        src="/{item.path}"
                        alt=""
                        loading="lazy"
                        class="rounded-xs border border-border bg-white object-contain p-1 block-16 inline-16"
                      />
                      <span class="grid gap-0.5 text-xs text-muted-foreground">
                        {#if item.valid_from != null || item.valid_to != null}
                          <span class="text-sm text-foreground"
                            >{period(
                              item.valid_from,
                              item.valid_from_precision,
                              item.valid_to,
                              item.valid_to_precision,
                            )}</span
                          >
                        {/if}
                        {#if item.license != null}
                          <span>{item.license}</span>
                        {/if}
                        {#if item.artist != null}
                          <span class="truncate"
                            >{m.edit_image_by({ artist: item.artist })}</span
                          >
                        {/if}
                        {#if item.source_url != null}
                          <a
                            class="underline underline-offset-2 hover:text-foreground"
                            href={item.source_url}
                            rel="external noopener noreferrer"
                            target="_blank">{m.edit_image_page()}</a
                          >
                        {/if}
                      </span>
                    </li>
                  {/each}
                </ul>
              {/if}
              {#if image != null}
                <div
                  class="contrib-settle mbs-2 grid gap-2 border-s-2 border-foreground ps-2 text-sm"
                >
                  <label class="grid gap-1 text-xs text-muted-foreground">
                    {m.edit_image_file()}
                    <Input
                      bind:value={image.file}
                      placeholder={m.edit_image_placeholder()}
                      aria-invalid={preview.state === "invalid" ||
                        preview.state === "missing" ||
                        (preview.state === "found" && !preview.free)}
                      autocomplete="off"
                      spellcheck={false}
                      maxlength={1000}
                      class="text-sm block-8"
                    />
                  </label>
                  {#if preview.state === "found"}
                    <div
                      class="grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-3"
                    >
                      <img
                        src={preview.thumb}
                        alt={m.edit_image_alt({ file: preview.name })}
                        referrerpolicy="no-referrer"
                        class={cn(
                          "rounded-xs border border-border bg-white object-contain p-1 block-16 inline-16",
                          { "opacity-50 grayscale": !preview.free },
                        )}
                      />
                      <span class="grid gap-0.5 text-xs text-muted-foreground">
                        <span class="truncate text-sm text-foreground"
                          >{preview.name}</span
                        >
                        <span
                          >{preview.license || m.edit_image_no_license()}</span
                        >
                        {#if preview.artist !== ""}
                          <span class="truncate"
                            >{m.edit_image_by({ artist: preview.artist })}</span
                          >
                        {/if}
                      </span>
                    </div>
                  {/if}
                  {#if !portrait}
                    <div class="grid grid-cols-2 gap-2">
                      <label class="grid gap-1 text-xs text-muted-foreground">
                        {m.edit_image_from()}
                        <Input
                          bind:value={image.from}
                          placeholder="1998-01-31"
                          inputmode="numeric"
                          autocomplete="off"
                          maxlength={10}
                          class="font-figures block-8"
                        />
                      </label>
                      <label class="grid gap-1 text-xs text-muted-foreground">
                        {m.edit_image_to()}
                        <Input
                          bind:value={image.to}
                          placeholder="2004-12-31"
                          inputmode="numeric"
                          autocomplete="off"
                          maxlength={10}
                          class="font-figures block-8"
                        />
                      </label>
                    </div>
                  {/if}
                  <div
                    class="flex items-start justify-between gap-2 text-xs text-muted-foreground"
                  >
                    <span class="text-pretty" role="status">
                      {#if imageProblem != null}
                        <span class="text-destructive">{imageProblem}</span>
                      {:else if preview.state === "looking"}
                        {m.edit_image_looking()}
                      {:else if preview.state === "unreachable"}
                        {m.edit_image_unreachable()}
                      {:else if preview.state === "idle"}
                        {m.edit_image_hint()}
                      {:else if dated}
                        {m.edit_image_dates_link()}
                      {:else if !portrait}
                        {m.edit_image_dates()}
                      {/if}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      class="shrink-0 px-2 text-xs block-7"
                      onclick={() => (image = null)}
                      >{m.edit_image_discard()}</Button
                    >
                  </div>
                </div>
              {:else if signedIn}
                <Button
                  variant="outline"
                  size="sm"
                  class="mbs-2"
                  onclick={() => (image = { file: "", from: "", to: "" })}
                  ><PlusIcon />{m.edit_image_add()}</Button
                >
              {/if}
            </section>
          {/if}

          {#if data.kind === "party"}
            <section class="border-be px-4 py-3" aria-labelledby="edit-colors">
              <h3 id="edit-colors" class="text-sm font-bold">
                {m.edit_colors()}
              </h3>
              {#if colors.length === 0 && newColor == null}
                <p class="mbs-1 text-sm text-muted-foreground">
                  {m.edit_colors_empty()}
                </p>
              {/if}
              <dl
                class="mbs-2 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm"
              >
                {#each colors as color (color.valid_from ?? "")}
                  {const draft = $derived(
                    colorDraft?.from === color.valid_from ? colorDraft : null,
                  )}
                  {const locked = $derived(colorEdit != null && draft == null)}
                  {const value = $derived(
                    draft?.kind === "set" ? draft.color : color.color,
                  )}
                  {const shown = $derived(
                    hex.test(value.trim()) ? value.trim() : color.color,
                  )}
                  {const dirty = $derived(
                    draft?.kind === "set" &&
                      value.trim().toLowerCase() !== color.color,
                  )}
                  {const end = $derived(
                    draft?.kind === "end" ? draft.to : null,
                  )}
                  <dt class="pbs-2 text-muted-foreground">
                    {period(
                      color.valid_from,
                      color.valid_from_precision,
                      color.valid_to,
                      color.valid_to_precision,
                    )}
                  </dt>
                  <dd class="grid gap-1 py-1">
                    <div class="flex items-center gap-2">
                      <span
                        class="shrink-0 rounded-xs border border-foreground/20 block-5 inline-5"
                        style:background-color={shown}
                        aria-hidden="true"
                      ></span>
                      <Input
                        {value}
                        readonly={!signedIn || locked || end != null}
                        aria-label={m.edit_color_value()}
                        aria-invalid={dirty && colorProblem != null}
                        autocomplete="off"
                        spellcheck={false}
                        maxlength={7}
                        class="border-transparent bg-transparent px-1.5 font-figures block-8 inline-28 hover:border-input"
                        oninput={(
                          event: Event & { currentTarget: HTMLInputElement },
                        ) => {
                          const next = event.currentTarget.value;
                          colorDraft =
                            next.trim().toLowerCase() === color.color
                              ? null
                              : {
                                  kind: "set",
                                  from: color.valid_from,
                                  color: next,
                                };
                        }}
                      />
                    </div>
                    {#if dirty}
                      <span class="ps-7 text-xs">
                        {#if colorProblem == null}
                          <Proof
                            class="contrib-settle"
                            before={color.color}
                            after={value.trim().toLowerCase()}
                          />
                        {:else}
                          <span class="text-destructive">{colorProblem}</span>
                        {/if}
                      </span>
                    {:else if end != null}
                      <div class="contrib-settle grid gap-1 ps-7">
                        <Input
                          value={end}
                          placeholder="1998-01-31"
                          inputmode="numeric"
                          aria-label={m.edit_color_end_date()}
                          aria-invalid={colorProblem != null}
                          autocomplete="off"
                          maxlength={10}
                          class="font-figures block-8 inline-36"
                          oninput={(
                            event: Event & { currentTarget: HTMLInputElement },
                          ) =>
                            (colorDraft = {
                              kind: "end",
                              from: color.valid_from,
                              to: event.currentTarget.value,
                            })}
                        />
                        <span class="text-xs">
                          {#if colorProblem != null}
                            <span class="text-destructive">{colorProblem}</span>
                          {:else if isDay(end.trim())}
                            <Proof
                              before={period(
                                color.valid_from,
                                color.valid_from_precision,
                                color.valid_to,
                                color.valid_to_precision,
                              )}
                              after={period(
                                color.valid_from,
                                color.valid_from_precision,
                                end.trim(),
                                "day",
                              )}
                            />
                          {/if}
                        </span>
                      </div>
                    {:else}
                      <span class="block ps-7 text-xs text-muted-foreground"
                        >{color.source ?? m.edit_no_source()}</span
                      >
                    {/if}
                    {#if signedIn && !locked && !dirty}
                      <div class="ps-5">
                        <Button
                          variant="ghost"
                          size="sm"
                          class="px-2 text-xs text-muted-foreground block-7"
                          onclick={() =>
                            (colorDraft =
                              end == null
                                ? {
                                    kind: "end",
                                    from: color.valid_from,
                                    to: "",
                                  }
                                : null)}
                          >{end == null
                            ? m.edit_color_end()
                            : m.edit_color_end_cancel()}</Button
                        >
                      </div>
                    {/if}
                  </dd>
                {/each}
                {#if newColor != null}
                  <dt class="pbs-2 font-semibold text-foreground">
                    {m.edit_color_add()}
                  </dt>
                  <dd
                    class="contrib-settle grid gap-2 border-s-2 border-foreground py-1 ps-2"
                  >
                    <div class="flex items-center gap-2">
                      <span
                        class={cn(
                          "shrink-0 rounded-xs border block-5 inline-5",
                          {
                            "border-foreground/20": hex.test(
                              newColor.color.trim(),
                            ),
                            "border-dashed border-input": !hex.test(
                              newColor.color.trim(),
                            ),
                          },
                        )}
                        style:background-color={hex.test(newColor.color.trim())
                          ? newColor.color.trim()
                          : null}
                        aria-hidden="true"
                      ></span>
                      <Input
                        bind:value={newColor.color}
                        placeholder="#1a2b3c"
                        aria-label={m.edit_color_value()}
                        aria-invalid={newColor.color.trim() !== "" &&
                          !hex.test(newColor.color.trim())}
                        autocomplete="off"
                        spellcheck={false}
                        maxlength={7}
                        class="font-figures block-8 inline-28"
                      />
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                      <label class="grid gap-1 text-xs text-muted-foreground">
                        {m.edit_color_from()}
                        <Input
                          bind:value={newColor.from}
                          placeholder="1998-01-31"
                          inputmode="numeric"
                          autocomplete="off"
                          maxlength={10}
                          class="font-figures block-8"
                        />
                      </label>
                      <label class="grid gap-1 text-xs text-muted-foreground">
                        {m.edit_color_to()}
                        <Input
                          bind:value={newColor.to}
                          placeholder="2004-12-31"
                          inputmode="numeric"
                          autocomplete="off"
                          maxlength={10}
                          class="font-figures block-8"
                        />
                      </label>
                    </div>
                    <div
                      class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                    >
                      {#if colorProblem == null}
                        <span>{m.edit_color_dates()}</span>
                      {:else}
                        <span class="text-destructive">{colorProblem}</span>
                      {/if}
                      <Button
                        variant="ghost"
                        size="sm"
                        class="shrink-0 px-2 text-xs block-7"
                        onclick={() => (newColor = null)}
                        >{m.edit_color_discard()}</Button
                      >
                    </div>
                  </dd>
                {/if}
              </dl>
              {#if signedIn}
                {#if colorEdit == null}
                  <Button
                    variant="outline"
                    size="sm"
                    class="mbs-2"
                    onclick={() => (newColor = { color: "", from: "", to: "" })}
                    ><PlusIcon />{m.edit_color_add()}</Button
                  >
                {:else}
                  <p class="mbs-2 text-xs text-pretty text-muted-foreground">
                    {m.edit_color_one()}
                  </p>
                {/if}
              {/if}
            </section>
          {/if}

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
                      <span class="flex shrink-0 items-center">
                        {#if !dropped && !dirty}
                          <Button
                            variant="ghost"
                            size="sm"
                            class="px-2 text-xs block-7"
                            onclick={() => translateNote(note)}
                            ><LanguagesIcon />{m.edit_translate()}</Button
                          >
                        {/if}
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
                      </span>
                    </div>
                  {/if}
                  {const drafted = $derived(
                    translations.filter((draft) => draft.note === note.id),
                  )}
                  {const held = $derived(
                    note.translations.filter(
                      (item) =>
                        !drafted.some((draft) => draft.lang === item.lang),
                    ),
                  )}
                  {#if held.length > 0 || drafted.length > 0}
                    <ul
                      class="grid gap-2 border-bs border-dashed ps-1.5 pbs-2"
                      aria-label={m.edit_translations()}
                    >
                      {#each held as item (item.lang)}
                        <li class="grid gap-0.5">
                          <div
                            class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                          >
                            <span class="flex flex-wrap gap-x-2">
                              <span class="font-semibold text-foreground"
                                >{languageName.of(item.lang) ?? item.lang}</span
                              >
                              <span>{originLabel[item.origin]()}</span>
                              {#if item.stale}
                                <span
                                  class="text-foreground underline decoration-dotted underline-offset-2"
                                  >{m.edit_translation_outdated()}</span
                                >
                              {/if}
                            </span>
                            {#if signedIn && !dropped && !dirty}
                              <Button
                                variant="ghost"
                                size="sm"
                                class="px-2 text-xs block-7"
                                onclick={() => {
                                  added += 1;
                                  translations.push({
                                    key: added,
                                    note: note.id,
                                    lang: item.lang,
                                    origin:
                                      item.origin === "machine"
                                        ? "human"
                                        : item.origin,
                                    body: item.body,
                                  });
                                }}>{m.edit_translation_revise()}</Button
                              >
                            {/if}
                          </div>
                          <p
                            class="line-clamp-3 text-pretty whitespace-pre-line"
                            lang={item.lang}
                            dir="auto"
                          >
                            {item.body}
                          </p>
                        </li>
                      {/each}
                      {#each drafted as draft (draft.key)}
                        {const problem = $derived(translationProblem(draft))}
                        {const replaced = $derived(
                          note.translations.find(
                            (item) => item.lang === draft.lang,
                          ),
                        )}
                        <li
                          class="contrib-settle grid gap-2 border-s-2 border-foreground ps-2"
                        >
                          <div
                            class="grid grid-cols-[minmax(0,1fr)_auto] gap-2"
                          >
                            <Combobox
                              items={languageItems.filter(
                                (option) =>
                                  option.value !== note.lang &&
                                  (option.value === draft.lang ||
                                    !drafted.some(
                                      (other) => other.lang === option.value,
                                    )),
                              )}
                              value={draft.lang}
                              label={m.edit_translation_lang()}
                              placeholder={m.edit_translation_lang()}
                              search={m.edit_note_lang_search()}
                              empty={m.edit_note_lang_empty()}
                              onValueChange={(next: string | null) => {
                                if (next != null) {
                                  draft.lang = next;
                                }
                              }}
                            />
                            <Select.Root
                              type="single"
                              value={draft.origin}
                              onValueChange={(next: string) => {
                                const origin = origins.find(
                                  (item) => item === next,
                                );
                                if (origin != null) {
                                  draft.origin = origin;
                                }
                              }}
                            >
                              <Select.Trigger
                                class="text-sm block-8"
                                aria-label={m.edit_translation_origin()}
                                >{originLabel[draft.origin]()}</Select.Trigger
                              >
                              <Select.Content>
                                {#each origins as origin (origin)}
                                  <Select.Item value={origin}
                                    >{originLabel[origin]()}</Select.Item
                                  >
                                {/each}
                              </Select.Content>
                            </Select.Root>
                          </div>
                          {#if replaced != null}
                            <p class="text-xs text-muted-foreground">
                              {replaced.stale
                                ? m.edit_translation_replaces_outdated()
                                : m.edit_translation_replaces()}
                            </p>
                          {/if}
                          <Textarea
                            value={draft.body}
                            lang={draft.lang ?? undefined}
                            dir="auto"
                            placeholder={m.edit_translation_placeholder()}
                            aria-label={m.edit_translation_body()}
                            aria-invalid={problem != null}
                            class="min-block-20 md:text-sm"
                            oninput={(
                              event: Event & {
                                currentTarget: HTMLTextAreaElement;
                              },
                            ) => (draft.body = event.currentTarget.value)}
                          />
                          <div
                            class="flex items-center justify-between gap-2 text-xs text-muted-foreground"
                          >
                            {#if problem != null}
                              <span class="text-pretty text-destructive"
                                >{problem}</span
                              >
                            {:else if draft.origin === "official"}
                              <span class="text-pretty"
                                >{m.edit_translation_official()}</span
                              >
                            {:else if draft.origin === "machine"}
                              <span class="text-pretty"
                                >{m.edit_translation_machine()}</span
                              >
                            {:else}
                              <span
                                >{m.edit_note_count({
                                  count: count(graphemes(draft.body)),
                                  limit: count(translationLimit(note.body_md)),
                                })}</span
                              >
                            {/if}
                            <Button
                              variant="ghost"
                              size="sm"
                              class="shrink-0 px-2 text-xs block-7"
                              onclick={() => {
                                translations = translations.filter(
                                  (other) => other.key !== draft.key,
                                );
                              }}>{m.edit_translation_discard()}</Button
                            >
                          </div>
                        </li>
                      {/each}
                    </ul>
                  {/if}
                </dd>
              {/each}
              {#each addedNotes as note, index (note.key)}
                {const kindName = $derived(noteKindLabel[note.noteKind]())}
                <dt class="pbs-2 font-semibold text-foreground">
                  {m.edit_note_add()}
                </dt>
                <dd
                  class="contrib-settle grid gap-2 border-s-2 border-foreground py-1 ps-2"
                >
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
                <dd
                  class="contrib-settle grid gap-1 border-s-2 border-foreground py-1 ps-2"
                >
                  <div class="grid grid-cols-[auto_minmax(0,1fr)] gap-2">
                    <Select.Root
                      type="single"
                      value={item.scheme}
                      onValueChange={(next: string) => {
                        const found = entitySchemes.find(
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
                        {#each entitySchemes as option (option)}
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

          <LabelsSection
            labels={data.labels}
            {signedIn}
            bind:changes={labelChanges}
            bind:invalid={badLabel}
          />

          <section class="border-be px-4 py-3" aria-labelledby="edit-citations">
            <h3 id="edit-citations" class="text-sm font-bold">
              {m.edit_citations()}
            </h3>
            {#if data.citations.length === 0 && !cite}
              <p class="mbs-1 text-sm text-muted-foreground">
                {m.edit_citations_empty()}
              </p>
            {/if}
            <ul class="mbs-2 grid gap-1.5 text-sm">
              {#each data.citations as citation (citation.id)}
                {const dropped = $derived(uncites.has(citation.id))}
                <li class="flex items-start justify-between gap-2">
                  <span
                    class={cn("grid min-inline-0", {
                      "contrib-settle text-muted-foreground line-through decoration-foreground/50":
                        dropped,
                    })}
                  >
                    {#if citation.url != null && !dropped}
                      <a
                        class="truncate underline decoration-foreground/30 underline-offset-2 hover:decoration-foreground"
                        href={citation.url}
                        rel="external noopener"
                        target="_blank">{citation.title}</a
                      >
                    {:else}
                      <span class="truncate">{citation.title}</span>
                    {/if}
                    <span class="truncate text-xs text-muted-foreground"
                      >{citation.publisher}</span
                    >
                  </span>
                  {#if signedIn && citation.own.length > 0}
                    <span class="flex shrink-0 items-center gap-2">
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
                          if (dropped) {
                            uncites.delete(citation.id);
                          } else {
                            uncites.add(citation.id);
                          }
                        }}
                        >{dropped
                          ? m.edit_citation_keep()
                          : m.edit_citation_remove()}</Button
                      >
                    </span>
                  {/if}
                </li>
              {/each}
              {#if cite}
                <li
                  class="contrib-settle grid gap-1 border-s-2 border-foreground py-1 ps-2"
                >
                  <span class="font-semibold">{m.edit_cite_new()}</span>
                  <span class="text-xs text-pretty text-muted-foreground"
                    >{m.edit_cite_body()}</span
                  >
                  <span class="flex justify-end">
                    <Button
                      variant="ghost"
                      size="sm"
                      class="px-2 text-xs block-7"
                      onclick={() => (cite = false)}
                      >{m.edit_cite_discard()}</Button
                    >
                  </span>
                </li>
              {/if}
            </ul>
            {#if signedIn && !cite}
              <Button
                variant="outline"
                size="sm"
                class="mbs-2"
                onclick={() => (cite = true)}
                ><PlusIcon />{m.edit_cite()}</Button
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
