<script lang="ts">
  import { resolve } from "$app/paths";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import PlusIcon from "@lucide/svelte/icons/plus";
  import XIcon from "@lucide/svelte/icons/x";
  import { tick, untrack } from "svelte";
  import type { Found } from "#lib/workspace.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { period, schemeLabel } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { recordKind, templateTitle } from "#lib/workspace.js";
  import type { PageData } from "./$types";
  import Cite from "./cite.svelte";
  import { Records } from "./records.svelte.js";
  import { Session } from "./session.svelte.js";
  import Shell from "./shell.svelte";

  const {
    data,
    workspace,
  }: {
    data: PageData;
    workspace: Extract<PageData["workspace"], { template: "merge" }>;
  } = $props();

  type Side = "record" | "into";
  type Pair = { key: number; record: string; into: string };

  let next = 0;
  const keyed = (record = "", into = ""): Pair => ({
    key: next++,
    record,
    into,
  });

  const initial = untrack(() => ({ ...data, workspace }));
  let pairs = $state<Array<Pair>>(
    initial.workspace.pairs.length === 0
      ? [keyed()]
      : initial.workspace.pairs.map((pair) => keyed(pair.record, pair.into)),
  );
  let summary = $state(initial.summary);
  let cited = $state(initial.workspace.evidence);
  const records = new Records(initial.records, () =>
    pairs.flatMap((pair) => [pair.record, pair.into]),
  );
  let checkRows = $state<Array<number>>([]);
  let selected = $state<number | null>(null);

  const fields = [
    ["name", m.ws_merge_field_name],
    ["kind", m.ws_merge_field_kind],
    ["polity", m.ws_merge_field_polity],
    ["dates", m.ws_merge_field_dates],
    ["ids", m.ws_merge_field_ids],
  ] as const;

  const shown = (entity: Found | null) =>
    entity == null
      ? null
      : {
          name: entity.name,
          kind: recordKind(entity.type),
          polity: entity.polity ?? "",
          dates:
            entity.from == null && entity.to == null
              ? ""
              : period(entity.from, "day", entity.to, "day"),
          ids: entity.ids
            .map((held) => `${schemeLabel[held.scheme]()} ${held.value}`)
            .join(", "),
        };

  const assessed = $derived.by(() => {
    const looked = pairs.map((pair) => {
      const sides = {
        record: records.get(pair.record),
        into: records.get(pair.into),
      };
      const entity = (side: Side) => {
        const found = sides[side];
        return found?.state === "found" ? found : null;
      };
      return { pair, sides, duplicate: entity("record"), keep: entity("into") };
    });
    const duplicates: Record<string, number> = {};
    for (const [index, item] of looked.entries()) {
      if (
        item.duplicate != null &&
        item.duplicate.id !== item.keep?.id &&
        duplicates[item.duplicate.id] == null
      ) {
        duplicates[item.duplicate.id] = index + 1;
      }
    }
    return looked.map((item, index) => {
      const n = index + 1;
      const { pair, sides, duplicate, keep } = item;
      let problem: string | null = null;
      let field: Side | null = null;
      for (const side of ["record", "into"] as const) {
        if (problem == null && sides[side]?.state === "missing") {
          problem = m.ws_record_missing();
          field = side;
        } else if (problem == null && sides[side]?.state === "retired") {
          problem = m.ws_record_retired();
          field = side;
        }
      }
      if (problem == null && duplicate != null && keep != null) {
        const first = duplicates[duplicate.id];
        const chained = duplicates[keep.id];
        if (duplicate.id === keep.id) {
          problem = m.ws_merge_same();
          field = "into";
        } else if (duplicate.type !== keep.type) {
          problem = m.ws_merge_types({
            duplicate: recordKind(duplicate.type),
            keep: recordKind(keep.type),
          });
          field = "into";
        } else if (first != null && first !== n) {
          problem = m.ws_merge_twice({ row: first });
          field = "record";
        } else if (chained != null) {
          problem = m.ws_merge_chain({ row: chained });
          field = "into";
        }
      }
      const warnings =
        duplicate == null || keep == null
          ? []
          : duplicate.ids.flatMap((held) => {
              const other = keep.ids.find(
                (kept) => kept.scheme === held.scheme,
              );
              return other == null || other.value === held.value
                ? []
                : [
                    m.ws_merge_id_conflict({
                      scheme: schemeLabel[held.scheme](),
                      duplicate: held.value,
                      keep: other.value,
                    }),
                  ];
            });
      return {
        pair,
        n,
        duplicate,
        keep,
        isBlank: pair.record.trim() === "" && pair.into.trim() === "",
        isLooking:
          (pair.record.trim() !== "" && sides.record == null) ||
          (pair.into.trim() !== "" && sides.into == null),
        isComplete: duplicate != null && keep != null,
        problem,
        field,
        warnings,
        sides: { record: shown(duplicate), into: shown(keep) },
      };
    });
  });

  const ready = $derived(
    assessed.flatMap((item) =>
      item.duplicate != null && item.keep != null && item.problem == null
        ? [{ n: item.n, id: item.duplicate.id, into: item.keep.id }]
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
      op: "merge" as const,
      id: item.id,
      into: item.into,
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
        template: "merge",
        pairs: assessed
          .filter((item) => !item.isBlank)
          .map(({ pair }) => ({ record: pair.record, into: pair.into })),
        evidence: cited,
      },
    }),
  );

  const canCheck = $derived(
    ready.length > 0 &&
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
          ? m.ws_submit_needs_pairs()
          : null,
      summary,
    ),
  );

  const names = $derived(
    Object.fromEntries(
      assessed.flatMap((item) =>
        [item.duplicate, item.keep].flatMap((entity) =>
          entity == null ? [] : [[entity.id, entity.name]],
        ),
      ),
    ),
  );

  const merges = $derived(
    (check?.diff?.rows ?? []).flatMap((row) => {
      const id = row.fields.entity_id?.[1];
      const into = row.fields.into_id?.[1];
      return row.table === "entity_merge" &&
        typeof id === "string" &&
        typeof into === "string"
        ? [{ id, into }]
        : [];
    }),
  );

  async function focusPair(key: number, side: Side = "record") {
    selected = key;
    await tick();
    document.getElementById(`ws-${side}-${key}`)?.focus();
  }

  function pickRow(n: number) {
    const item = assessed[n - 1];
    if (item != null) {
      void focusPair(item.pair.key, item.field ?? "record");
    }
  }

  function pickEntity(id: string) {
    const item = assessed.find((entry) => entry.duplicate?.id === id);
    if (item != null) {
      void focusPair(item.pair.key);
    }
  }

  function addPair() {
    const pair = keyed();
    pairs.push(pair);
    void focusPair(pair.key);
  }

  function removePair(key: number) {
    pairs = pairs.filter((pair) => pair.key !== key);
    if (pairs.length === 0) {
      pairs = [keyed()];
    }
  }

  function swap(pair: Pair) {
    [pair.record, pair.into] = [pair.into, pair.record];
  }

  function swapSelected() {
    const pair = pairs.find((item) => item.key === selected);
    if (pair != null) {
      swap(pair);
    }
  }

  function paste(event: ClipboardEvent, index: number) {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    if (!/[\t\n]/u.test(text.trim())) {
      return;
    }
    event.preventDefault();
    const pasted: Array<Pair> = [];
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
      pasted.push(keyed(parts[0] ?? "", parts[1] ?? ""));
    }
    const current = pairs[index];
    const replaces =
      current?.record.trim() === "" && current.into.trim() === "";
    pairs.splice(index, replaces ? 1 : 0, ...pasted);
    pairs = pairs.slice(0, 500);
  }

  function onkeepkey(event: KeyboardEvent, index: number) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    const following = pairs[index + 1];
    if (following == null) {
      addPair();
    } else {
      void focusPair(following.key);
    }
  }
</script>

<Shell
  {session}
  number={data.number}
  heading={templateTitle.merge()}
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
    {#if assessed.every((item) => item.duplicate == null)}
      <p class="px-4 text-pretty text-muted-foreground">
        {m.ws_outline_empty()}
      </p>
    {:else}
      <ul class="grid px-2 pbe-3">
        {#each assessed as item (item.pair.key)}
          {#if item.duplicate != null}
            {const isWrong = $derived(
              item.problem != null || failure?.row === item.n,
            )}
            <li>
              <button
                type="button"
                class={cn(
                  "grid gap-0.5 rounded-sm px-2 py-1.5 text-start inline-full hover:bg-muted",
                  { "bg-muted": selected === item.pair.key },
                )}
                onclick={async () => focusPair(item.pair.key)}
              >
                <span class="flex items-baseline justify-between gap-2">
                  <span class="truncate font-semibold min-inline-0"
                    >{item.duplicate.name}</span
                  >
                  {#if isWrong}
                    <span class="font-figures font-semibold text-destructive"
                      >{item.n}</span
                    >
                  {/if}
                </span>
                <span class="truncate text-xs text-muted-foreground"
                  >{item.keep == null
                    ? recordKind(item.duplicate.type)
                    : `${m.ws_merge_into()} ${item.keep.name}`}</span
                >
              </button>
            </li>
          {/if}
        {/each}
      </ul>
    {/if}
  {/snippet}

  {#snippet editor()}
    <section class="p-4" aria-labelledby="ws-merge">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 pbe-2">
        <h2 id="ws-merge" class="font-semibold">{m.ws_merge_title()}</h2>
        <span class="text-xs text-muted-foreground">{m.ws_merge_hint()}</span>
      </div>
      <div
        class="grid grid-cols-[2rem_minmax(0,1fr)_2rem_minmax(0,1fr)_2rem] gap-x-2 border-s-2 border-be border-s-transparent py-1 ps-1 text-muted-foreground"
        aria-hidden="true"
      >
        <span></span>
        <span class="ps-1">{m.ws_col_duplicate()}</span>
        <span></span>
        <span class="ps-1">{m.ws_col_keep()}</span>
        <span></span>
      </div>
      <ol class="grid">
        {#each pairs as pair, index (pair.key)}
          {const item = $derived(assessed[index] ?? null)}
          {const label = $derived(m.ws_row_label({ row: index + 1 }))}
          {const isFailed = $derived(failure?.row === index + 1)}
          {const isWrong = $derived(item?.problem != null || isFailed)}
          {const isReady = $derived(
            item != null && item.isComplete && !isWrong,
          )}
          {const sides = $derived(item?.sides ?? null)}
          <li
            class={cn(
              "grid grid-cols-[2rem_minmax(0,1fr)_2rem_minmax(0,1fr)_2rem] gap-x-2 gap-y-1 border-s-2 border-be py-1.5 ps-1",
              {
                "border-s-foreground": isReady,
                "border-s-destructive": isWrong,
                "border-s-transparent": !isReady && !isWrong,
              },
              { "bg-muted": selected === pair.key },
            )}
          >
            <span
              class="pbs-1.5 text-end font-figures text-xs text-muted-foreground"
              >{index + 1}</span
            >
            {#each ["record", "into"] as const as side (side)}
              <Input
                id="ws-{side}-{pair.key}"
                bind:value={pair[side]}
                disabled={isLocked}
                placeholder={m.ws_record_placeholder()}
                aria-label="{label}, {side === 'record'
                  ? m.ws_col_duplicate()
                  : m.ws_col_keep()}"
                aria-invalid={item?.field === side}
                autocomplete="off"
                spellcheck={false}
                maxlength={2048}
                class={cn("block-8", { "col-4": side === "into" })}
                onfocus={() => (selected = pair.key)}
                onpaste={(event: ClipboardEvent) => paste(event, index)}
                onkeydown={side === "into"
                  ? (event: KeyboardEvent) => onkeepkey(event, index)
                  : undefined}
              />
              {#if side === "record"}
                <Button
                  variant="ghost"
                  size="icon"
                  class="text-muted-foreground block-8 inline-8"
                  disabled={isLocked}
                  aria-label={m.ws_merge_swap({ row: index + 1 })}
                  title={m.ws_merge_swap({ row: index + 1 })}
                  onclick={() => swap(pair)}><ArrowLeftRightIcon /></Button
                >
              {/if}
            {/each}
            <Button
              variant="ghost"
              size="icon"
              class="text-muted-foreground block-8 inline-8"
              disabled={isLocked}
              aria-label={m.ws_remove_row({ row: index + 1 })}
              onclick={() => removePair(pair.key)}><XIcon /></Button
            >
            {#if sides != null && (sides.record != null || sides.into != null)}
              <dl class="col-span-full grid grid-cols-subgrid pbs-1 text-xs">
                {#each fields.filter(([key]) => (sides.record?.[key] ?? "") !== "" || (sides.into?.[key] ?? "") !== "") as [key, name] (key)}
                  {const before = $derived(sides.record?.[key] ?? "")}
                  {const after = $derived(sides.into?.[key] ?? "")}
                  {const differs = $derived(
                    sides.record != null &&
                      sides.into != null &&
                      before !== after,
                  )}
                  <div class="col-span-full grid grid-cols-subgrid">
                    <dt class="sr-only">{name()}</dt>
                    <dd
                      class={cn(
                        "col-2 py-px ps-1 text-pretty wrap-break-word",
                        { "text-muted-foreground": !differs },
                      )}
                    >
                      {#if key === "name" && item?.duplicate != null}
                        <a
                          class="font-semibold text-foreground underline decoration-border underline-offset-4"
                          href={resolve("/id/[id]", { id: item.duplicate.id })}
                          target="_blank"
                          rel="noopener">{before}</a
                        >
                      {:else}
                        <span class="font-figures">{before}</span>
                      {/if}
                    </dd>
                    <dd
                      class={cn("col-3 py-px text-center font-figures", {
                        "font-semibold": differs,
                        "text-muted-foreground": !differs,
                      })}
                      aria-hidden="true"
                      title={name()}
                    >
                      {sides.record != null && sides.into != null
                        ? differs
                          ? "≠"
                          : "="
                        : ""}
                    </dd>
                    <dd
                      class={cn(
                        "col-4 border-s-2 border-foreground py-px ps-2 text-pretty wrap-break-word",
                        { "text-muted-foreground": !differs },
                      )}
                    >
                      {#if key === "name" && item?.keep != null}
                        <a
                          class="font-semibold text-foreground underline decoration-border underline-offset-4"
                          href={resolve("/id/[id]", { id: item.keep.id })}
                          target="_blank"
                          rel="noopener">{after}</a
                        >
                      {:else}
                        <span class="font-figures">{after}</span>
                      {/if}
                    </dd>
                  </div>
                {/each}
              </dl>
            {/if}
            {#if item != null && (isWrong || item.isLooking || item.warnings.length > 0)}
              <div class="col-[2/-1] grid gap-0.5 ps-1 text-xs">
                {#if item.problem != null}
                  <p class="font-semibold text-destructive">{item.problem}</p>
                {:else if isFailed}
                  <p class="font-semibold text-destructive">{failure?.text}</p>
                {:else if item.isLooking}
                  <p class="text-muted-foreground">{m.ws_record_looking()}</p>
                {/if}
                {#each item.warnings as warning (warning)}
                  <p class="font-semibold">{warning}</p>
                {/each}
              </div>
            {/if}
          </li>
        {/each}
      </ol>
      {#if !isLocked}
        <Button variant="outline" size="sm" class="mbs-3" onclick={addPair}
          ><PlusIcon />{m.ws_add_row()}</Button
        >
      {/if}
    </section>
  {/snippet}

  {#snippet diff()}
    {#if merges.length === 0}
      <p class="text-pretty text-muted-foreground">{m.ws_diff_empty()}</p>
    {:else}
      {#if session.isStale}
        <p class="pbe-2 text-xs font-semibold">{m.ws_checks_stale()}</p>
      {/if}
      <section>
        <h3 class="text-xs text-muted-foreground">{m.ws_diff_merged()}</h3>
        <ul class="mbs-1 grid border-s-2 border-foreground">
          {#each merges as merge (merge.id)}
            <li>
              <button
                type="button"
                class="grid gap-0.5 border-be py-1.5 ps-3 text-start inline-full hover:bg-muted"
                onclick={() => pickEntity(merge.id)}
              >
                <span class="truncate font-semibold"
                  >{names[merge.id] ?? merge.id}</span
                >
                <span class="truncate text-xs"
                  >{m.ws_merge_into()} {names[merge.into] ?? merge.into}</span
                >
              </button>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/snippet}

  {#snippet evidence()}
    <Cite bind:cited {isLocked} empty={m.ws_merge_evidence_empty()} />
  {/snippet}

  {#snippet commands(act: (action: () => void) => void)}
    <Command.Item disabled={isLocked} onSelect={() => act(addPair)}
      >{m.ws_add_row()}</Command.Item
    >
    <Command.Item
      disabled={isLocked || selected == null}
      onSelect={() => act(swapSelected)}
      >{m.ws_merge_swap({
        row: pairs.findIndex((pair) => pair.key === selected) + 1,
      })}</Command.Item
    >
  {/snippet}
</Shell>
