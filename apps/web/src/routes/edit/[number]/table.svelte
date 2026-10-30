<script lang="ts">
  import { resolve } from "$app/paths";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import { SvelteMap } from "svelte/reactivity";
  import type { Cells, Column, Table } from "#lib/workspace.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import {
    kindLabel,
    nameKinds,
    schemeExample,
    schemeLabel,
    schemes,
    sourceKindLabel,
    sourceKinds,
  } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import {
    blank,
    columns,
    described,
    holdings,
    isBlank,
    isComplete,
    judge,
    languageItems,
    languageLabel,
    needsSource,
    op,
    parse,
    recordKind,
    repeats,
    summarize,
    swatch,
    templateTitle,
  } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: Table }>;
  } = $props();

  type Row = { key: number; cells: Cells };

  const template = untrack(() => workspace.template);
  const fields = columns[template];
  const languages = languageItems();

  let next = 0;
  const keyed = (cells: Cells): Row => ({
    key: next++,
    cells: { record: "", ...blank(template), ...cells },
  });

  const initial = untrack(() => ({ ...data, workspace }));
  let rows = $state<Array<Row>>(
    initial.workspace.rows.length === 0
      ? [keyed({})]
      : initial.workspace.rows.map((row) => keyed(row)),
  );
  let summary = $state(initial.summary);
  let cited = $state(initial.workspace.evidence);
  const records = new SvelteMap<string, PageData["records"][string] | null>(
    Object.entries(initial.records),
  );
  let checkRows = $state<Array<number>>([]);
  let selected = $state<string | null>(null);

  const columnLabel: Record<Exclude<Column, "record">, () => string> = {
    scheme: m.ws_col_scheme,
    value: m.ws_col_value,
    text: m.ws_col_text,
    lang: m.ws_col_lang,
    kind: m.ws_col_kind,
    color: m.ws_col_color,
    from: m.ws_col_from,
    to: m.ws_col_to,
  };

  const placeholderOf = (column: Column, cells: Cells) => {
    if (column === "value") {
      const scheme = schemes.find((option) => option === cells.scheme);
      return scheme == null ? "" : schemeExample[scheme];
    }
    return column === "color"
      ? "#1a2b3c"
      : column === "from" || column === "to"
        ? "YYYY-MM-DD"
        : "";
  };

  const assessed = $derived.by(() => {
    const seen: Record<string, number> = {};
    return rows.map((row, index) => {
      const n = index + 1;
      const record = (row.cells.record ?? "").trim();
      const found = record === "" ? undefined : records.get(record);
      const entity = found?.state === "found" ? found : null;
      let problem: string | null = null;
      let field: Column | null = null;
      if (found?.state === "missing") {
        problem = m.ws_record_missing();
        field = "record";
      } else if (found?.state === "retired") {
        problem = m.ws_record_retired();
        field = "record";
      } else {
        const judged = judge(template, row.cells, entity);
        if (judged != null) {
          problem = judged.problem;
          field = judged.field;
        } else if (entity != null && isComplete(template, row.cells)) {
          const repeat = repeats(template, row.cells, entity.id);
          const earlier = seen[repeat.key];
          if (earlier == null) {
            seen[repeat.key] = n;
          } else {
            problem = repeat.problem(earlier);
            field = repeat.field;
          }
        }
      }
      return {
        row,
        n,
        isBlank: isBlank(row.cells),
        entity,
        isLooking: record !== "" && found == null,
        isComplete: entity != null && isComplete(template, row.cells),
        problem,
        field,
      };
    });
  });

  const ready = $derived(
    assessed.flatMap((item) =>
      item.entity != null && item.isComplete && item.problem == null
        ? [{ n: item.n, entity: item.entity, cells: item.row.cells }]
        : [],
    ),
  );

  const source = $derived.by(() => {
    if (cited == null) {
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

  const ops = $derived(
    ready.map((item) => ({
      ...op(template, item.cells, item.entity.id),
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
        template,
        rows: assessed
          .filter((item) => !item.isBlank)
          .map(({ row }) => row.cells),
        evidence: cited,
      },
    }),
  );

  const canCheck = $derived(
    ready.length > 0 &&
      (!needsSource[template] || source != null) &&
      assessed.every((item) => item.problem == null && !item.isLooking),
  );

  const session = new Session(
    untrack(() => data.number),
    untrack(() => data.check),
    {
      serialized: () => serialized,
      compiled: () => compiled,
      canCheck: () => canCheck,
      checked: () => {
        checkRows = ready.map((item) => item.n);
      },
    },
  );

  checkRows = untrack(() => ready.map((item) => item.n));

  const check = $derived(session.check);
  const isLocked = $derived(session.isLocked);

  const failure = $derived.by(() => {
    const error = session.error;
    if (error == null) {
      return null;
    }
    return {
      row: error.op == null ? null : (checkRows[error.op] ?? null),
      text: error.text,
    };
  });

  const blocker = $derived(
    session.blocker(
      assessed.some((item) => item.problem != null) || failure != null
        ? m.ws_submit_needs_fix()
        : ready.length === 0 ||
            assessed.some((item) => !item.isBlank && !item.isComplete)
          ? m.ws_submit_needs_rows()
          : needsSource[template] && source == null
            ? m.ws_submit_needs_source()
            : null,
      summary,
    ),
  );

  const groups = $derived.by(() => {
    const groups: Record<
      string,
      {
        id: string;
        name: string;
        type: string;
        adds: Array<{ key: number; text: string; color: string | null }>;
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
      if (item.isComplete) {
        group.adds.push({
          key: item.row.key,
          text: summarize(template, item.row.cells),
          color: swatch(template, item.row.cells),
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
    Object.fromEntries(groups.map((group) => [group.id, group.name])),
  );

  const changes = $derived(
    (
      [
        ["add", "+", m.ws_diff_added],
        ["change", "~", m.ws_diff_changed],
        ["drop", "−", m.ws_diff_removed],
      ] as const
    )
      .map(([kind, mark, label]) => ({
        kind,
        mark,
        label,
        rows: (check?.diff?.rows ?? [])
          .filter(
            (row) =>
              row.op === kind &&
              ["external_id", "name", "party_color"].includes(row.table),
          )
          .map((row) => ({ entity: row.entity_id ?? "", ...described(row) })),
      }))
      .filter((group) => group.rows.length > 0),
  );

  $effect(() => {
    const wanted = [
      ...new Set(rows.map((row) => (row.cells.record ?? "").trim())),
    ].filter((ref) => ref !== "" && !records.has(ref));
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
      ? ((await response.json()) as Record<string, PageData["records"][string]>)
      : {};
    for (const ref of refs) {
      records.set(ref, found[ref] ?? { state: "missing" });
    }
  }

  async function focusRow(key: number, field: Column = "record") {
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
    const row = keyed({});
    rows.push(row);
    void focusRow(row.key);
  }

  function removeRow(key: number) {
    rows = rows.filter((row) => row.key !== key);
    if (rows.length === 0) {
      rows = [keyed({})];
    }
  }

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
      const parts = (
        line.includes("\t") ? line.split("\t") : line.split(",")
      ).map((cell) => cell.trim().replace(/^"(.*)"$/u, "$1"));
      if (pasted.length === 0 && !/[/\d]/u.test(parts[0] ?? "")) {
        continue;
      }
      pasted.push(keyed(parse(template, parts)));
    }
    const current = rows[index];
    const replaces = current != null && isBlank(current.cells);
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
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle[template]()}
  bind:summary
  {blocker}
  failure={failure == null
    ? null
    : {
        label:
          failure.row == null ? null : m.ws_finding_row({ row: failure.row }),
        text: failure.text,
        pick: failure.row == null ? null : () => pickRow(failure.row ?? 1),
      }}
>
  {#snippet outline()}
    {#if groups.length === 0}
      <p class="px-4 text-pretty text-muted-foreground">
        {m.ws_outline_empty()}
      </p>
    {:else}
      <ul class="grid px-2 pbe-3">
        {#each groups as group (group.id)}
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
                <span class="truncate font-semibold min-inline-0"
                  >{group.name}</span
                >
                {#if group.problems > 0}
                  <span class="font-figures font-semibold text-destructive"
                    >{group.problems}</span
                  >
                {/if}
              </span>
              <span class="text-xs text-muted-foreground"
                >{recordKind(group.type)}</span
              >
              {#each group.adds as add (add.key)}
                <span class="flex items-center gap-1.5 text-xs min-inline-0"
                  >+{#if add.color != null}<span
                      class="shrink-0 rounded-xs ring-1 ring-foreground/20 block-2.5 inline-2.5"
                      style:background-color={add.color}
                    ></span>{/if}<span class="truncate font-figures"
                    >{add.text}</span
                  ></span
                >
              {/each}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  {/snippet}

  {#snippet editor()}
    <section class="p-4" aria-labelledby="ws-table">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 pbe-2">
        <h2 id="ws-table" class="font-semibold">
          {template === "identifiers"
            ? m.ws_table_title()
            : template === "names"
              ? m.edit_names()
              : m.ws_table_colors()}
        </h2>
        <span class="text-xs text-muted-foreground"
          >{template === "identifiers"
            ? m.ws_table_hint()
            : template === "names"
              ? m.ws_table_hint_names()
              : m.ws_table_hint_colors()}</span
        >
      </div>
      <div
        class={cn(
          "grid gap-x-2 border-s-2 border-be border-s-transparent py-1 ps-1 text-muted-foreground max-sm:hidden",
          {
            "grid-cols-[2rem_minmax(0,1fr)_11.5rem_9rem_2rem]":
              template === "identifiers",
            "grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)_10rem_9.5rem_2rem]":
              template === "names",
            "grid-cols-[2rem_minmax(0,1fr)_8.5rem_8.5rem_8.5rem_2rem]":
              template === "colors",
          },
        )}
        aria-hidden="true"
      >
        <span></span>
        <span class="ps-1">{m.ws_col_record()}</span>
        {#each fields as column (column)}
          <span class="ps-1">{columnLabel[column]()}</span>
        {/each}
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
              "grid grid-cols-[2rem_minmax(0,1fr)_2rem] gap-x-2 gap-y-1 border-s-2 border-be py-1.5 ps-1",
              {
                "sm:grid-cols-[2rem_minmax(0,1fr)_11.5rem_9rem_2rem]":
                  template === "identifiers",
                "sm:grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)_10rem_9.5rem_2rem]":
                  template === "names",
                "sm:grid-cols-[2rem_minmax(0,1fr)_8.5rem_8.5rem_8.5rem_2rem]":
                  template === "colors",
              },
              {
                "border-s-foreground": isReady,
                "border-s-destructive": isWrong,
                "border-s-transparent": !isReady && !isWrong,
              },
              { "bg-muted": selected != null && selected === item?.entity?.id },
            )}
          >
            <span
              class="pbs-1.5 text-end font-figures text-xs text-muted-foreground"
              >{index + 1}</span
            >
            <Input
              id="ws-record-{row.key}"
              bind:value={row.cells.record}
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
            <div
              class={cn("col-2 grid gap-2 sm:contents", {
                "grid-cols-2": template !== "colors",
                "grid-cols-3": template === "colors",
              })}
            >
              {#each fields as column (column)}
                {const named = $derived(`${label}, ${columnLabel[column]()}`)}
                {const isInvalid = $derived(item?.field === column)}
                {#if column === "scheme"}
                  {const scheme = $derived(
                    schemes.find((option) => option === row.cells.scheme),
                  )}
                  <Select.Root
                    type="single"
                    value={row.cells.scheme}
                    disabled={isLocked}
                    onValueChange={(chosen: string) => {
                      row.cells.scheme = chosen;
                    }}
                  >
                    <Select.Trigger
                      class="text-sm block-8 inline-full"
                      aria-label={named}
                      aria-invalid={isInvalid}
                      >{scheme == null
                        ? m.ws_col_scheme()
                        : schemeLabel[scheme]()}</Select.Trigger
                    >
                    <Select.Content>
                      {#each schemes as option (option)}
                        <Select.Item value={option}
                          >{schemeLabel[option]()}</Select.Item
                        >
                      {/each}
                    </Select.Content>
                  </Select.Root>
                {:else if column === "kind"}
                  {const kind = $derived(
                    nameKinds.find((option) => option === row.cells.kind),
                  )}
                  <Select.Root
                    type="single"
                    value={row.cells.kind}
                    disabled={isLocked}
                    onValueChange={(chosen: string) => {
                      row.cells.kind = chosen;
                    }}
                  >
                    <Select.Trigger
                      class="text-sm block-8 inline-full"
                      aria-label={named}
                      aria-invalid={isInvalid}
                      >{kind == null
                        ? (row.cells.kind ?? "") || m.ws_col_kind()
                        : kindLabel[kind]()}</Select.Trigger
                    >
                    <Select.Content>
                      {#each nameKinds as option (option)}
                        <Select.Item value={option}
                          >{kindLabel[option]()}</Select.Item
                        >
                      {/each}
                    </Select.Content>
                  </Select.Root>
                {:else if column === "lang"}
                  {#if isLocked}
                    <Input
                      value={languageLabel(row.cells.lang ?? "")}
                      disabled
                      aria-label={named}
                      class="block-8"
                    />
                  {:else}
                    <Combobox
                      items={languages}
                      value={row.cells.lang ?? null}
                      label={named}
                      placeholder={m.ws_col_lang()}
                      search={m.edit_note_lang_search()}
                      empty={m.edit_note_lang_empty()}
                      class="block-8 inline-full"
                      onValueChange={(chosen: string | null) => {
                        row.cells.lang = chosen ?? "";
                      }}
                    />
                  {/if}
                {:else}
                  {const color = $derived(
                    column === "color" ? swatch(template, row.cells) : null,
                  )}
                  <div
                    class={cn("relative", {
                      "max-sm:col-span-2": column === "text",
                    })}
                  >
                    {#if column === "color"}
                      <span
                        class="pointer-events-none absolute inset-s-2 inset-bs-1/2 -translate-y-1/2 rounded-xs ring-1 ring-foreground/20 block-4 inline-4"
                        style:background-color={color ?? "transparent"}
                        aria-hidden="true"
                      ></span>
                    {/if}
                    <Input
                      id="ws-{column}-{row.key}"
                      bind:value={row.cells[column]}
                      disabled={isLocked}
                      placeholder={placeholderOf(column, row.cells)}
                      aria-label={named}
                      aria-invalid={isInvalid}
                      autocomplete="off"
                      spellcheck={column === "text"}
                      maxlength={column === "text"
                        ? 500
                        : column === "value"
                          ? 100
                          : 20}
                      inputmode={column === "from" || column === "to"
                        ? "numeric"
                        : undefined}
                      class={cn("block-8", {
                        "font-figures": column !== "text",
                        "ps-8": column === "color",
                      })}
                      onkeydown={(event: KeyboardEvent) =>
                        onvaluekey(event, index)}
                    />
                  </div>
                {/if}
              {/each}
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
                  · {[
                    recordKind(item.entity.type),
                    ...holdings(template, item.entity).slice(0, 6),
                  ].join(" · ")}
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
  {/snippet}

  {#snippet diff()}
    {#if changes.length === 0}
      <p class="text-pretty text-muted-foreground">
        {m.ws_diff_empty()}
      </p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <div class="grid gap-4">
        {#each changes as group (group.kind)}
          <section>
            <h3 class="text-xs text-muted-foreground">
              {group.label()}
            </h3>
            <ul class="mbs-1 grid border-s-2 border-foreground">
              {#each group.rows as row, i (i)}
                <li>
                  <button
                    type="button"
                    class={cn(
                      "grid gap-0.5 border-be py-1.5 ps-3 text-start inline-full hover:bg-muted",
                      { "bg-muted": selected === row.entity },
                    )}
                    onclick={() => pickEntity(row.entity)}
                  >
                    <span class="truncate font-semibold"
                      >{entityNames[row.entity] ?? row.entity}</span
                    >
                    <span class="flex items-center gap-1.5 text-xs"
                      >{group.mark}{#if row.color != null}<span
                          class="shrink-0 rounded-xs ring-1 ring-foreground/20 block-2.5 inline-2.5"
                          style:background-color={row.color}
                        ></span>{/if}<span class="font-figures">{row.text}</span
                      ></span
                    >
                  </button>
                </li>
              {/each}
            </ul>
          </section>
        {/each}
      </div>
    {/if}
  {/snippet}

  {#snippet evidence()}
    {#if cited == null}
      <p class="text-pretty text-muted-foreground">
        {needsSource[template] ? m.ws_evidence_needed() : m.ws_evidence_empty()}
      </p>
      {#if !isLocked}
        <Button
          variant="outline"
          size="sm"
          class="mbs-3"
          onclick={() =>
            (cited = {
              url: "",
              title: "",
              publisher: "",
              kind: "official_body",
            })}><PlusIcon />{m.ws_evidence_add()}</Button
        >
      {/if}
    {:else}
      {const chosen = $derived(cited.kind)}
      <fieldset class="contrib-settle grid gap-2" disabled={isLocked}>
        <legend class="pbe-2 font-semibold">{m.edit_evidence()}</legend>
        <Input
          type="url"
          inputmode="url"
          bind:value={cited.url}
          placeholder="https://"
          aria-label={m.edit_url()}
          maxlength={2048}
          class="block-8"
        />
        <Input
          bind:value={cited.title}
          placeholder={m.edit_url_title_placeholder()}
          aria-label={m.edit_url_title()}
          maxlength={500}
          class="block-8"
        />
        <Input
          bind:value={cited.publisher}
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
            if (kind != null && cited != null) {
              cited.kind = kind;
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
              <Select.Item value={kind}>{sourceKindLabel[kind]()}</Select.Item>
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
            onclick={() => (cited = null)}>{m.ws_evidence_remove()}</Button
          >
        {/if}
      </fieldset>
    {/if}
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item disabled={isLocked} onSelect={() => act(addRow)}
      >{m.ws_add_row()}</Command.Item
    >
  {/snippet}
</Shell>
