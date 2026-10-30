<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import { resolve } from "$app/paths";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import { SvelteMap } from "svelte/reactivity";
  import type { Scheme } from "#lib/contrib.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import {
    partySchemes,
    personSchemes,
    schemeExample,
    schemeLabel,
    schemePattern,
    schemes,
    sourceKindLabel,
    sourceKinds,
    tierLabel,
  } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  type Row = {
    key: number;
    record: string;
    scheme: Scheme | "";
    value: string;
  };

  let next = 0;
  const keyed = (row: Omit<Row, "key">): Row => ({ key: next++, ...row });
  const blank = () => keyed({ record: "", scheme: "", value: "" });

  const initial = untrack(() => data);
  let rows = $state<Array<Row>>(
    initial.workspace.rows.length === 0
      ? [blank()]
      : initial.workspace.rows.map((row) => keyed(row)),
  );
  let summary = $state(initial.summary);
  let evidence = $state(initial.workspace.evidence);
  const records = new SvelteMap<string, PageData["records"][string] | null>(
    Object.entries(initial.records),
  );
  let check = $state(initial.check);
  let checkRows = $state<Array<number>>([]);
  let checkedFor = $state<string | null>(null);
  let checkError = $state<string | null>(null);
  let isChecking = $state(false);
  let saved = $state<string | null>(null);
  let saveError = $state<string | null>(null);
  let isSaving = $state(false);
  let isSubmitting = $state(false);
  let submitted = $state<number | null>(null);
  let submitError = $state<string | null>(null);
  let selected = $state<string | null>(null);
  let tab = $state<"diff" | "checks" | "evidence">("checks");
  let palette = $state(false);

  const tabs = [
    ["diff", m.ws_tab_diff],
    ["checks", m.ws_tab_checks],
    ["evidence", m.ws_tab_evidence],
  ] as const;

  const fits = (type: string, scheme: Scheme) =>
    type === "person"
      ? personSchemes.some((item) => item === scheme)
      : type === "party"
        ? partySchemes.some((item) => item === scheme)
        : scheme === "wikidata";

  const kindOf = (type: string) =>
    type === "person"
      ? m.ws_kind_person()
      : type === "party"
        ? m.ws_kind_party()
        : m.ws_kind_other();

  const assessed = $derived.by(() => {
    const seen: Record<string, number> = {};
    return rows.map((row, index) => {
      const n = index + 1;
      const record = row.record.trim();
      const value = row.value.trim();
      const isBlank = record === "" && row.scheme === "" && value === "";
      const found = record === "" ? undefined : records.get(record);
      const entity = found?.state === "found" ? found : null;
      let problem: string | null = null;
      let field: "record" | "scheme" | "value" | null = null;
      if (found?.state === "missing") {
        problem = m.ws_record_missing();
        field = "record";
      } else if (found?.state === "retired") {
        problem = m.ws_record_retired();
        field = "record";
      } else if (
        row.scheme !== "" &&
        value !== "" &&
        !schemePattern[row.scheme].test(value)
      ) {
        problem = m.ws_value_invalid({ scheme: schemeLabel[row.scheme]() });
        field = "value";
      } else if (
        entity != null &&
        row.scheme !== "" &&
        !fits(entity.type, row.scheme)
      ) {
        problem = m.ws_scheme_unfit({ scheme: schemeLabel[row.scheme]() });
        field = "scheme";
      } else if (row.scheme !== "" && value !== "") {
        const earlier = seen[`${row.scheme}:${value}`];
        if (earlier == null) {
          seen[`${row.scheme}:${value}`] = n;
        } else {
          problem = m.ws_row_duplicate({ row: earlier });
          field = "value";
        }
      }
      return {
        row,
        n,
        isBlank,
        entity,
        isLooking: record !== "" && found == null,
        isComplete: entity != null && row.scheme !== "" && value !== "",
        problem,
        field,
      };
    });
  });

  const ready = $derived(
    assessed.flatMap((item) =>
      item.entity != null &&
      item.row.scheme !== "" &&
      item.isComplete &&
      item.problem == null
        ? [
            {
              n: item.n,
              key: item.row.key,
              entity: item.entity,
              scheme: item.row.scheme,
              value: item.row.value.trim(),
            },
          ]
        : [],
    ),
  );

  const source = $derived.by(() => {
    if (evidence == null) {
      return null;
    }
    const url = evidence.url.trim();
    const title = evidence.title.trim();
    const publisher = evidence.publisher.trim();
    return /^https?:\/\//iu.test(url) &&
      URL.canParse(url) &&
      title !== "" &&
      publisher !== ""
      ? { url, title, publisher, kind: evidence.kind }
      : null;
  });

  const ops = $derived(
    ready.map((item) => ({
      op: "add",
      table: "external_id",
      values: {
        entity_id: item.entity.id,
        scheme: item.scheme,
        value: item.value,
      },
      ...(source != null && { evidence: ["e1"] }),
    })),
  );

  const compiled = $derived(JSON.stringify([ops, source]));

  const serialized = $derived(
    JSON.stringify({
      summary: summary.trim(),
      ops,
      ...(source != null && { evidence: { e1: source } }),
      workspace: {
        template: "identifiers",
        rows: assessed
          .filter((item) => !item.isBlank)
          .map(({ row }) => ({
            record: row.record,
            scheme: row.scheme,
            value: row.value,
          })),
        evidence,
      },
    }),
  );

  const isStale = $derived(check != null && compiled !== checkedFor);

  const failure = $derived.by(() => {
    if (check?.status !== "failed" || check.error == null) {
      return null;
    }
    const text = check.error.replace(/^\w+Error: /u, "");
    const at = /^ops\[(\d+)\][.\w]*: /u.exec(text);
    return {
      row: at == null ? null : (checkRows[Number(at[1])] ?? null),
      text: at == null ? text : text.slice(at[0].length),
    };
  });

  const findings = $derived(
    Object.entries(check?.checks?.findings ?? {}).filter(
      ([, value]) => value > 0,
    ),
  );

  const findingCount = $derived.by(() => {
    let total = 0;
    for (const [, value] of findings) {
      total += value;
    }
    return total;
  });

  const isLocked = $derived(submitted != null);

  const blocker = $derived(
    assessed.some((item) => item.problem != null) || failure != null
      ? m.ws_submit_needs_fix()
      : ready.length === 0 ||
          assessed.some((item) => !item.isBlank && !item.isComplete)
        ? m.ws_submit_needs_rows()
        : summary.trim() === ""
          ? m.ws_submit_needs_summary()
          : check?.status !== "done" || isStale
            ? m.ws_submit_needs_check()
            : null,
  );

  const outline = $derived.by(() => {
    const groups: Record<
      string,
      {
        id: string;
        name: string;
        type: string;
        adds: Array<{ key: number; scheme: Scheme; value: string }>;
        problems: number;
      }
    > = {};
    for (const item of assessed) {
      if (item.entity == null) {
        continue;
      }
      const group = groups[item.entity.id] ?? {
        id: item.entity.id,
        name: item.entity.name,
        type: item.entity.type,
        adds: [],
        problems: 0,
      };
      if (item.row.scheme !== "" && item.row.value.trim() !== "") {
        group.adds.push({
          key: item.row.key,
          scheme: item.row.scheme,
          value: item.row.value.trim(),
        });
      }
      if (item.problem != null || failure?.row === item.n) {
        group.problems += 1;
      }
      groups[item.entity.id] = group;
    }
    return Object.values(groups);
  });

  const entityNames = $derived(
    Object.fromEntries(outline.map((group) => [group.id, group.name])),
  );

  const added = $derived(
    (check?.diff?.rows ?? []).filter(
      (row) => row.table === "external_id" && row.op === "add",
    ),
  );

  const saveStatus = $derived(
    saveError == null
      ? isSaving
        ? m.ws_saving()
        : serialized === saved
          ? m.ws_saved()
          : m.ws_unsaved()
      : m.ws_save_failed({ reason: saveError }),
  );

  const clock = (value: string) =>
    new Intl.DateTimeFormat(getLocale(), { timeStyle: "short" }).format(
      new Date(value.replace(" ", "T").replace(/([+-]\d{2})$/u, "$1:00")),
    );

  saved = untrack(() => serialized);
  checkedFor = untrack(() => (initial.check == null ? null : compiled));
  checkRows = untrack(() => ready.map((item) => item.n));

  async function read<T>(response: Response) {
    return response.json() as Promise<T>;
  }

  async function reason(response: Response) {
    try {
      const body = await read<{ message?: string }>(response);
      return body.message ?? response.statusText;
    } catch {
      return response.statusText;
    }
  }

  let queue = Promise.resolve(true);

  async function save() {
    queue = queue.then(async () => {
      const body = serialized;
      if (body === saved) {
        return true;
      }
      isSaving = true;
      try {
        const response = await fetch(`/edit/${data.number}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body,
        });
        if (!response.ok) {
          saveError = await reason(response);
          return false;
        }
        saved = body;
        saveError = null;
        return true;
      } catch (error_) {
        saveError = error_ instanceof Error ? error_.message : String(error_);
        return false;
      } finally {
        isSaving = false;
      }
    });
    return queue;
  }

  $effect(() => {
    if (serialized === saved || isLocked) {
      return;
    }
    const timer = setTimeout(() => void save(), 800);
    return () => clearTimeout(timer);
  });

  $effect(() => {
    const wanted = [...new Set(rows.map((row) => row.record.trim()))].filter(
      (ref) => ref !== "" && !records.has(ref),
    );
    if (wanted.length === 0) {
      return;
    }
    const timer = setTimeout(() => void look(wanted), 400);
    return () => clearTimeout(timer);
  });

  async function look(refs: Array<string>) {
    for (const ref of refs) {
      records.set(ref, null);
    }
    const response = await fetch("/edit/records", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refs }),
    });
    const found = response.ok
      ? await read<Record<string, PageData["records"][string]>>(response)
      : {};
    for (const ref of refs) {
      records.set(ref, found[ref] ?? { state: "missing" });
    }
  }

  const canCheck = $derived(
    !isLocked &&
      ready.length > 0 &&
      assessed.every((item) => item.problem == null && !item.isLooking),
  );

  $effect(() => {
    if (!canCheck || isChecking || compiled === checkedFor) {
      return;
    }
    const timer = setTimeout(() => void runChecks(), 3000);
    return () => clearTimeout(timer);
  });

  async function runChecks() {
    if (isChecking || ready.length === 0 || isLocked) {
      return;
    }
    isChecking = true;
    checkError = null;
    const snapshot = compiled;
    const at = ready.map((item) => item.n);
    try {
      if (!(await save())) {
        return;
      }
      const response = await fetch(`/edit/${data.number}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ do: "check" }),
      });
      if (!response.ok) {
        checkError = await reason(response);
        return;
      }
      const body = await read<{ check: PageData["check"] }>(response);
      check = body.check;
      checkedFor = snapshot;
      checkRows = at;
    } finally {
      isChecking = false;
    }
  }

  async function submit() {
    if (blocker != null || isSubmitting || isLocked) {
      return;
    }
    isSubmitting = true;
    submitError = null;
    try {
      if (!(await save())) {
        return;
      }
      const response = await fetch(`/edit/${data.number}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ do: "submit" }),
      });
      if (!response.ok) {
        submitError = await reason(response);
        return;
      }
      const body = await read<{ check: PageData["check"] }>(response);
      submitError =
        body.check?.status === "failed"
          ? (body.check.error?.replace(/^\w+Error: /u, "") ?? null)
          : null;
      submitted = data.number;
    } finally {
      isSubmitting = false;
    }
  }

  async function focusRow(key: number, field: "record" | "value" = "record") {
    await tick();
    document.getElementById(`ws-${field}-${key}`)?.focus();
  }

  function pickEntity(id: string) {
    selected = id;
    const item = assessed.find((entry) => entry.entity?.id === id);
    if (item != null) {
      void focusRow(item.row.key);
    }
  }

  function pickRow(n: number) {
    const item = assessed[n - 1];
    if (item != null) {
      selected = item.entity?.id ?? null;
      void focusRow(item.row.key);
    }
  }

  function addRow() {
    const row = blank();
    rows.push(row);
    void focusRow(row.key);
  }

  function removeRow(key: number) {
    rows = rows.filter((row) => row.key !== key);
    if (rows.length === 0) {
      rows = [blank()];
    }
  }

  const guess = (value: string): Scheme | "" =>
    /^Q[1-9]\d*$/u.test(value)
      ? "wikidata"
      : /^[A-Z]\d{6}$/u.test(value)
        ? "bioguide"
        : "";

  const schemeOf = (text: string, value: string): Scheme | "" => {
    const lower = text.trim().toLowerCase();
    return (
      schemes.find(
        (scheme) =>
          scheme === lower || schemeLabel[scheme]().toLowerCase() === lower,
      ) ?? guess(value)
    );
  };

  function paste(event: ClipboardEvent, index: number) {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    if (!/[\t\n]/u.test(text.trim())) {
      return;
    }
    event.preventDefault();
    const pasted: Array<Row> = [];
    for (const line of text.replaceAll("\r", "").split("\n")) {
      if (line.trim() === "") {
        continue;
      }
      const cells = (
        line.includes("\t") ? line.split("\t") : line.split(",")
      ).map((cell) => cell.trim().replace(/^"(.*)"$/u, "$1"));
      const record = cells[0] ?? "";
      if (pasted.length === 0 && !/[/\d]/u.test(record)) {
        continue;
      }
      const value = (cells.length >= 3 ? cells[2] : cells[1]) ?? "";
      pasted.push(
        keyed({
          record,
          scheme:
            cells.length >= 3 ? schemeOf(cells[1] ?? "", value) : guess(value),
          value,
        }),
      );
    }
    const current = rows[index];
    const replaces =
      current?.record.trim() === "" && current.value.trim() === "";
    rows.splice(index, replaces ? 1 : 0, ...pasted);
    rows = rows.slice(0, 500);
  }

  function onvaluekey(event: KeyboardEvent, index: number) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    const following = rows[index + 1];
    if (following == null) {
      addRow();
    } else {
      void focusRow(following.key);
    }
  }

  function onkeydowncapture(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      event.stopPropagation();
      palette = !palette;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      tab = "checks";
      void runChecks();
    }
  }

  function act(action: () => void) {
    palette = false;
    action();
  }
</script>

<svelte:window {onkeydowncapture} />

<svelte:head>
  <title>{m.ws_draft_number({ number: data.number })} | {m.wordmark()}</title>
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
        >{m.ws_draft_number({ number: data.number })}</span
      >
      <span class="truncate">{summary.trim() || m.ws_summary_default()}</span>
    </h1>
    <p
      class="ms-auto flex items-baseline gap-4 text-muted-foreground"
      aria-live="polite"
    >
      <span class={cn({ "font-semibold text-destructive": saveError != null })}
        >{saveStatus}</span
      >
      {#if check?.tier != null && !isStale}
        <span class="font-figures">{m.ws_tier({ tier: check.tier })}</span>
      {/if}
      {#if findingCount > 0}
        <button
          type="button"
          class="font-semibold text-foreground"
          onclick={() => (tab = "checks")}
          >{m.proposal_findings({ count: findingCount })}</button
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
      {#if outline.length === 0}
        <p class="px-4 text-pretty text-muted-foreground">
          {m.ws_outline_empty()}
        </p>
      {:else}
        <ul class="grid px-2 pbe-3">
          {#each outline as group (group.id)}
            <li>
              <button
                type="button"
                class={cn(
                  "grid gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
                  { "bg-muted": selected === group.id },
                )}
                onclick={() => pickEntity(group.id)}
              >
                <span class="flex items-baseline justify-between gap-2">
                  <span class="truncate font-semibold">{group.name}</span>
                  {#if group.problems > 0}
                    <span class="font-figures font-semibold text-destructive"
                      >{group.problems}</span
                    >
                  {/if}
                </span>
                <span class="text-xs text-muted-foreground"
                  >{kindOf(group.type)}</span
                >
                {#each group.adds as add (add.key)}
                  <span class="font-figures text-xs"
                    >+ {schemeLabel[add.scheme]()} {add.value}</span
                  >
                {/each}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </nav>

    <main class="lg:overflow-y-auto lg:min-block-0">
      <section class="p-4" aria-labelledby="ws-table">
        <div
          class="flex flex-wrap items-baseline justify-between gap-x-4 pbe-2"
        >
          <h2 id="ws-table" class="font-semibold">{m.ws_table_title()}</h2>
          <span class="text-xs text-muted-foreground">{m.ws_table_hint()}</span>
        </div>
        <div
          class="grid grid-cols-[2.25rem_minmax(0,1fr)_9rem_10rem_2rem] gap-x-2 border-be py-1 text-muted-foreground max-sm:hidden"
          aria-hidden="true"
        >
          <span></span>
          <span class="ps-1">{m.ws_col_record()}</span>
          <span class="ps-1">{m.ws_col_scheme()}</span>
          <span class="ps-1">{m.ws_col_value()}</span>
          <span></span>
        </div>
        <ol class="grid">
          {#each rows as row, index (row.key)}
            {const item = $derived(assessed[index] ?? null)}
            {const label = $derived(m.ws_row_label({ row: index + 1 }))}
            {const isFailed = $derived(failure?.row === index + 1)}
            {const isWrong = $derived(item?.problem != null || isFailed)}
            {const isReady = $derived(
              item != null && item.isComplete && !isWrong,
            )}
            <li
              class={cn(
                "grid grid-cols-[2rem_minmax(0,1fr)_2rem] gap-x-2 gap-y-1 border-s-2 border-be py-1.5 ps-1 sm:grid-cols-[2rem_minmax(0,1fr)_9rem_10rem_2rem]",
                {
                  "border-s-foreground": isReady,
                  "border-s-destructive": isWrong,
                  "border-s-transparent": !isReady && !isWrong,
                },
                {
                  "bg-muted": selected != null && selected === item?.entity?.id,
                },
              )}
            >
              <span
                class="pbs-1.5 text-end font-figures text-xs text-muted-foreground"
                >{index + 1}</span
              >
              <Input
                id="ws-record-{row.key}"
                bind:value={row.record}
                disabled={isLocked}
                placeholder={m.ws_record_placeholder()}
                aria-label="{label}, {m.ws_col_record()}"
                aria-invalid={item?.field === "record"}
                autocomplete="off"
                spellcheck={false}
                maxlength={2048}
                class="block-8"
                onfocus={() => (selected = item?.entity?.id ?? null)}
                onpaste={(event: ClipboardEvent) => paste(event, index)}
              />
              <div class="col-2 grid grid-cols-2 gap-2 sm:contents">
                <Select.Root
                  type="single"
                  value={row.scheme}
                  disabled={isLocked}
                  onValueChange={(next: string) => {
                    row.scheme =
                      schemes.find((scheme) => scheme === next) ?? "";
                  }}
                >
                  <Select.Trigger
                    class="text-sm block-8 inline-full"
                    aria-label="{label}, {m.ws_col_scheme()}"
                    aria-invalid={item?.field === "scheme"}
                    >{row.scheme === ""
                      ? m.ws_col_scheme()
                      : schemeLabel[row.scheme]()}</Select.Trigger
                  >
                  <Select.Content>
                    {#each schemes as scheme (scheme)}
                      <Select.Item value={scheme}
                        >{schemeLabel[scheme]()}</Select.Item
                      >
                    {/each}
                  </Select.Content>
                </Select.Root>
                <Input
                  id="ws-value-{row.key}"
                  bind:value={row.value}
                  disabled={isLocked}
                  placeholder={row.scheme === ""
                    ? ""
                    : schemeExample[row.scheme]}
                  aria-label="{label}, {m.ws_col_value()}"
                  aria-invalid={item?.field === "value"}
                  autocomplete="off"
                  spellcheck={false}
                  maxlength={100}
                  class="font-figures block-8"
                  onkeydown={(event: KeyboardEvent) => onvaluekey(event, index)}
                />
              </div>
              <Button
                variant="ghost"
                size="icon"
                class="text-muted-foreground block-8 inline-8 max-sm:col-start-3 max-sm:row-start-1"
                disabled={isLocked}
                aria-label={m.ws_remove_row({ row: index + 1 })}
                onclick={() => removeRow(row.key)}><XIcon /></Button
              >
              {#if item != null && (isWrong || item.isLooking || item.entity != null)}
                <p
                  class={cn("col-[2/-1] text-xs", {
                    "font-semibold text-destructive": isWrong,
                    "text-muted-foreground": !isWrong,
                  })}
                >
                  {#if item.problem != null}
                    {item.problem}
                  {:else if isFailed}
                    {failure?.text}
                  {:else if item.isLooking}
                    {m.ws_record_looking()}
                  {:else if item.entity != null}
                    <a
                      class="underline decoration-border underline-offset-4 hover:text-foreground"
                      href={resolve("/id/[id]", { id: item.entity.id })}
                      target="_blank"
                      rel="noopener">{item.entity.name}</a
                    >
                    · {kindOf(
                      item.entity.type,
                    )}{#each item.entity.ids as held (held.scheme + held.value)}
                      · {schemeLabel[held.scheme]()}
                      <span class="font-figures">{held.value}</span>
                    {/each}
                  {/if}
                </p>
              {/if}
            </li>
          {/each}
        </ol>
        {#if !isLocked}
          <Button variant="outline" size="sm" class="mbs-3" onclick={addRow}
            ><PlusIcon />{m.ws_add_row()}</Button
          >
        {/if}
      </section>
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
            aria-selected={tab === key}
            aria-controls="ws-panel"
            class={cn("px-2 py-1.5", {
              "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                tab === key,
              "text-muted-foreground hover:text-foreground": tab !== key,
            })}
            onclick={() => (tab = key)}
            >{label()}{#if key === "checks" && findingCount > 0}<span
                class="ms-1 font-figures">{findingCount}</span
              >{/if}</button
          >
        {/each}
      </div>
      <div
        id="ws-panel"
        class="overflow-y-auto p-4"
        role="tabpanel"
        aria-labelledby="ws-tab-{tab}"
      >
        {#if tab === "diff"}
          {#if added.length === 0}
            <p class="text-pretty text-muted-foreground">
              {m.ws_diff_empty()}
            </p>
          {:else}
            {#if isStale}
              <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
            {/if}
            <p class="text-xs text-muted-foreground">{m.ws_diff_added()}</p>
            <ul class="mbs-1 grid border-s-2 border-foreground">
              {#each added as row, i (i)}
                {const entity = $derived(row.entity_id ?? "")}
                <li>
                  <button
                    type="button"
                    class={cn(
                      "grid gap-0.5 border-be py-1.5 ps-3 text-start inline-full hover:bg-muted",
                      { "bg-muted": selected === entity },
                    )}
                    onclick={() => pickEntity(entity)}
                  >
                    <span class="truncate font-semibold"
                      >{entityNames[entity] ?? entity}</span
                    >
                    <span class="font-figures text-xs"
                      >+ {row.key.scheme} {row.key.value}</span
                    >
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        {:else if tab === "checks"}
          {#if isChecking}
            <p class="text-muted-foreground" role="status">
              {m.ws_checks_running()}
            </p>
          {:else if checkError != null}
            <p class="font-semibold text-destructive">{checkError}</p>
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
                {#if isStale}
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
                      {#if failure.row != null}
                        {const row = $derived(failure.row)}
                        <button
                          type="button"
                          class="font-semibold underline underline-offset-4"
                          onclick={() => pickRow(row)}
                          >{m.ws_finding_row({ row })}</button
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
                {#if findings.length === 0}
                  <p class="border-s-2 border-foreground ps-3 font-semibold">
                    {m.ws_checks_passed()}
                  </p>
                {:else}
                  <ul class="grid border-s-2 border-foreground">
                    {#each findings as [code, value] (code)}
                      <li
                        class="flex items-baseline justify-between gap-2 border-be py-1.5 ps-3"
                      >
                        <span class="font-figures">{code}</span>
                        <span class="font-figures font-semibold">{value}</span>
                      </li>
                    {/each}
                  </ul>
                {/if}
              {:else}
                <p class="text-muted-foreground">{m.ws_checks_running()}</p>
              {/if}
            </div>
          {/if}
        {:else if evidence == null}
          <p class="text-pretty text-muted-foreground">
            {m.ws_evidence_empty()}
          </p>
          {#if !isLocked}
            <Button
              variant="outline"
              size="sm"
              class="mbs-3"
              onclick={() =>
                (evidence = {
                  url: "",
                  title: "",
                  publisher: "",
                  kind: "official_body",
                })}><PlusIcon />{m.ws_evidence_add()}</Button
            >
          {/if}
        {:else}
          {const chosen = $derived(evidence.kind)}
          <fieldset class="contrib-settle grid gap-2" disabled={isLocked}>
            <legend class="pbe-2 font-semibold">{m.edit_evidence()}</legend>
            <Input
              type="url"
              inputmode="url"
              bind:value={evidence.url}
              placeholder="https://"
              aria-label={m.edit_url()}
              maxlength={2048}
              class="block-8"
            />
            <Input
              bind:value={evidence.title}
              placeholder={m.edit_url_title_placeholder()}
              aria-label={m.edit_url_title()}
              maxlength={500}
              class="block-8"
            />
            <Input
              bind:value={evidence.publisher}
              placeholder={m.edit_url_publisher_placeholder()}
              aria-label={m.edit_url_publisher()}
              maxlength={200}
              class="block-8"
            />
            <Select.Root
              type="single"
              value={chosen}
              onValueChange={(next: string) => {
                const kind = sourceKinds.find((item) => item === next);
                if (kind != null && evidence != null) {
                  evidence.kind = kind;
                }
              }}
            >
              <Select.Trigger
                class="text-sm block-8 inline-full"
                aria-label={m.edit_url_kind()}
                >{sourceKindLabel[chosen]()}</Select.Trigger
              >
              <Select.Content>
                {#each sourceKinds as kind (kind)}
                  <Select.Item value={kind}
                    >{sourceKindLabel[kind]()}</Select.Item
                  >
                {/each}
              </Select.Content>
            </Select.Root>
            <p class="text-xs text-pretty text-muted-foreground">
              {m.edit_url_note()}
            </p>
            {#if !isLocked}
              <Button
                variant="ghost"
                size="sm"
                class="justify-self-start px-2 text-xs text-muted-foreground"
                onclick={() => (evidence = null)}
                >{m.ws_evidence_remove()}</Button
              >
            {/if}
          </fieldset>
        {/if}
      </div>
    </aside>
  </div>

  <footer class="flex flex-wrap items-center gap-3 border-bs px-4 py-2.5">
    <Button
      variant="ghost"
      size="sm"
      class="max-sm:hidden"
      onclick={() => (palette = true)}>{m.ws_commands()} <kbd>⌘K</kbd></Button
    >
    <Button
      variant="outline"
      size="sm"
      disabled={isLocked || isChecking || ready.length === 0}
      onclick={() => {
        tab = "checks";
        void runChecks();
      }}>{m.ws_run_checks()} <kbd class="max-sm:hidden">⌘↵</kbd></Button
    >
    <Input
      bind:value={summary}
      disabled={isLocked}
      class="flex-1 basis-64 block-8 min-inline-0"
      aria-label={m.edit_summary()}
      placeholder={m.edit_summary_placeholder()}
      maxlength={300}
    />
    {#if submitted != null}
      <p class="grid gap-0.5" role="status">
        <span class="font-semibold"
          >{m.ws_submitted({ number: submitted })}</span
        >
        <a
          class="underline underline-offset-4"
          href={resolve("/proposals/[number]", { number: String(submitted) })}
          >{m.ws_go_proposal()}</a
        >
        {#if submitError != null}
          <span class="text-xs font-semibold text-destructive"
            >{submitError}</span
          >
        {/if}
      </p>
    {:else}
      <div class="flex items-center gap-3">
        {#if submitError != null || blocker != null}
          <span
            class={cn("text-xs text-pretty max-inline-56", {
              "font-semibold text-destructive": submitError != null,
              "text-muted-foreground": submitError == null,
            })}>{submitError ?? blocker}</span
          >
        {/if}
        <Button
          size="sm"
          disabled={blocker != null || isSubmitting}
          onclick={submit}>{m.ws_submit()}</Button
        >
      </div>
    {/if}
  </footer>
</div>

<Command.Dialog
  bind:open={palette}
  title={m.ws_commands()}
  description={m.ws_commands_hint()}
  class="sm:max-inline-lg"
>
  <Command.Input placeholder={m.ws_commands_hint()} />
  <Command.List>
    <Command.Empty>{m.ws_commands_none()}</Command.Empty>
    <Command.Group>
      <Command.Item
        disabled={isLocked || ready.length === 0}
        onSelect={() =>
          act(() => {
            tab = "checks";
            void runChecks();
          })}
        >{m.ws_run_checks()}<Command.Shortcut>⌘↵</Command.Shortcut
        ></Command.Item
      >
      <Command.Item disabled={isLocked} onSelect={() => act(addRow)}
        >{m.ws_add_row()}</Command.Item
      >
      {#if failure?.row != null}
        {const row = $derived(failure.row)}
        <Command.Item onSelect={() => act(() => pickRow(row))}
          >{m.ws_finding_row({ row })}</Command.Item
        >
      {/if}
      {#each tabs as [key, label] (key)}
        <Command.Item onSelect={() => act(() => (tab = key))}
          >{m.ws_inspector()}: {label()}</Command.Item
        >
      {/each}
      <Command.Item
        disabled={blocker != null || isLocked}
        onSelect={() => act(() => void submit())}>{m.ws_submit()}</Command.Item
      >
      <Command.LinkItem href={resolve("/edit")}>{m.ws_back()}</Command.LinkItem>
    </Command.Group>
  </Command.List>
</Command.Dialog>
