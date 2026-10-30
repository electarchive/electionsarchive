<script lang="ts">
  import type { Cell, Change, Editable } from "#lib/contrib.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Select from "#lib/components/ui/select/index.js";
  import { measureLabel, stageLabel, stages } from "#lib/contrib.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    results,
    signedIn,
    change = $bindable(null),
    invalid = $bindable(false),
  }: {
    results: NonNullable<Editable["results"]>;
    signedIn: boolean;
    change?: Change | null;
    invalid?: boolean;
  } = $props();

  type Loaded = {
    total: number;
    cells: Array<{
      unit: string;
      scheme: string;
      code: string;
      name: string;
      option: string | null;
      measure: string;
      channel: string;
      count_round: number;
      value: number;
    }>;
  };
  type Row = {
    key: string;
    label: string;
    detail: string;
    unit: string | { scheme: string; code: string };
  };

  const LIMIT = 2000;
  const UNIT_MEASURES = [
    "registered",
    "ballots_cast",
    "valid",
    "invalid",
    "blank",
  ];

  let editing = $state<
    { mode: "correct"; set: string } | { mode: "add" } | null
  >(null);
  let loaded = $state<Loaded | null>(null);
  let loading = $state(false);
  let failed = $state<string | null>(null);
  let values = $state<Record<string, string>>({});
  let added = $state<Array<Row>>([]);
  let stage = $state<(typeof stages)[number]>("certified");
  let scheme = $state("");
  let code = $state("");

  const corrected = $derived.by(() => {
    const target = editing?.mode === "correct" ? editing.set : null;
    return results.sets.find((set) => set.id === target);
  });
  const shown = $derived(
    (loaded?.cells ?? []).filter(
      (cell) =>
        cell.channel === "all" &&
        cell.count_round === 0 &&
        (cell.option == null
          ? UNIT_MEASURES.includes(cell.measure)
          : cell.measure === "votes"),
    ),
  );
  const columns = $derived.by(() => {
    const held = new Set(
      shown.map((cell) => cell.option ?? `measure:${cell.measure}`),
    );
    const fresh = editing?.mode === "add";
    return [
      ...results.options
        .filter((option) => fresh || held.has(option.id))
        .map((option) => ({
          key: option.id,
          label: option.label,
          option: option.id,
          measure: "votes",
        })),
      ...UNIT_MEASURES.filter(
        (measure) =>
          held.has(`measure:${measure}`) ||
          (fresh && (measure === "valid" || measure === "ballots_cast")),
      ).map((measure) => ({
        key: `measure:${measure}`,
        label: measureLabel(measure),
        option: null,
        measure,
      })),
    ];
  });
  const rows = $derived.by(() => {
    const found: Array<Row> = [];
    for (const cell of loaded?.cells ?? []) {
      if (found.at(-1)?.key !== cell.unit) {
        found.push({
          key: cell.unit,
          label: cell.name,
          detail: `${cell.scheme} ${cell.code}`,
          unit: cell.unit,
        });
      }
    }
    return [...found, ...added];
  });
  const original = $derived(
    new Map(
      editing?.mode === "correct"
        ? shown.map((cell) => [
            `${cell.unit}|${cell.option ?? `measure:${cell.measure}`}`,
            cell.value,
          ])
        : [],
    ),
  );
  const parse = (raw: string) => {
    const text = raw.replaceAll(/[\s,]/gu, "");
    if (text === "") {
      return null;
    }
    return /^\d+(?:\.\d+)?$/u.test(text) ? Number(text) : Number.NaN;
  };
  const figure = (row: Row, column: { key: string }) => {
    const key = `${row.key}|${column.key}`;
    const raw = values[key];
    return raw == null ? (original.get(key) ?? null) : parse(raw);
  };
  const totals = $derived(
    columns.map((column) => {
      let sum = 0;
      for (const row of rows) {
        const value = figure(row, column);
        if (value != null && !Number.isNaN(value)) {
          sum += value;
        }
      }
      return sum;
    }),
  );
  const optionColumns = $derived(
    columns.filter((column) => column.option != null),
  );
  const firstMeasure = $derived(
    columns.find((column) => column.option == null)?.key,
  );
  const validColumn = $derived(
    columns.find((column) => column.key === "measure:valid"),
  );
  const sums = $derived(
    new Map(
      rows.map((row) => {
        let sum = 0;
        for (const column of optionColumns) {
          const value = figure(row, column);
          if (value != null && !Number.isNaN(value)) {
            sum += value;
          }
        }
        return [row.key, sum];
      }),
    ),
  );
  const mismatched = $derived(
    new Set(
      validColumn == null
        ? []
        : rows
            .filter((row) => {
              const valid = figure(row, validColumn);
              return (
                valid != null &&
                !Number.isNaN(valid) &&
                Math.abs((sums.get(row.key) ?? 0) - valid) > 0.5
              );
            })
            .map((row) => row.key),
    ),
  );
  const cells = $derived.by(() => {
    const list: Array<Cell> = [];
    for (const row of rows) {
      for (const column of columns) {
        const key = `${row.key}|${column.key}`;
        const raw = values[key];
        if (raw == null) {
          continue;
        }
        const value = parse(raw);
        if (value == null || Number.isNaN(value)) {
          continue;
        }
        if (editing?.mode === "correct" && original.get(key) === value) {
          continue;
        }
        list.push({
          unit: row.unit,
          option: column.option,
          measure: column.measure,
          channel: "all",
          count_round: 0,
          value,
        });
      }
    }
    return list;
  });
  const broken = $derived(
    Object.values(values).some((raw) => Number.isNaN(parse(raw))),
  );

  $effect(() => {
    invalid = broken;
    change =
      cells.length === 0 || editing == null
        ? null
        : editing.mode === "correct"
          ? { kind: "correct", resultSet: editing.set, cells }
          : { kind: "results", stage, rows: cells };
  });

  const load = async (set: string | undefined) => {
    loaded = null;
    failed = null;
    if (set == null) {
      return;
    }
    loading = true;
    try {
      const response = await fetch(
        `/api/v1/result-sets/${set}/cells?limit=${LIMIT}`,
      );
      if (!response.ok) {
        failed = m.edit_results_failed({ reason: response.statusText });
        return;
      }
      loaded = (await response.json()) as Loaded;
      scheme = loaded.cells[0]?.scheme ?? scheme;
    } finally {
      loading = false;
    }
  };

  const start = (next: { mode: "correct"; set: string } | { mode: "add" }) => {
    editing = next;
    values = {};
    added = [];
    void load(
      next.mode === "correct"
        ? next.set
        : (results.sets.find((set) => set.is_default) ?? results.sets[0])?.id,
    );
  };

  const stop = () => {
    editing = null;
    loaded = null;
    values = {};
    added = [];
  };

  const addPlace = () => {
    const unit = { scheme: scheme.trim(), code: code.trim() };
    const key = `new:${unit.scheme}:${unit.code}`;
    if (
      unit.scheme === "" ||
      unit.code === "" ||
      rows.some((row) => row.key === key)
    ) {
      return;
    }
    added = [...added, { key, label: unit.code, detail: unit.scheme, unit }];
    code = "";
  };
</script>

<section class="border-be px-4 py-3" aria-labelledby="edit-results">
  <h3 id="edit-results" class="text-sm font-bold">{m.edit_results()}</h3>
  {#if results.sets.length === 0}
    <p class="mbs-1 text-sm text-muted-foreground">
      {m.edit_results_empty()}
    </p>
  {/if}
  <ul class="mbs-2 grid gap-1.5 text-sm">
    {#each results.sets as set (set.id)}
      <li
        class={cn(
          "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 rounded-sm px-1.5 py-1",
          { "bg-muted": editing?.mode === "correct" && editing.set === set.id },
        )}
      >
        <span class="grid min-inline-0">
          <span class="font-semibold"
            >{stageLabel[
              stages.find((item) => item === set.stage) ?? "official"
            ]()}{#if set.is_default}<span
                class="ms-2 text-xs font-normal text-muted-foreground"
                >{m.edit_results_default()}</span
              >{/if}</span
          >
          <span class="truncate text-xs text-muted-foreground"
            >{set.source ?? set.pipeline} · {m.proposal_results_count({
              count: set.cells,
            })}</span
          >
        </span>
        {#if signedIn && editing == null}
          <Button
            variant="ghost"
            size="sm"
            class="px-2 text-xs block-7"
            onclick={() => start({ mode: "correct", set: set.id })}
            >{m.edit_results_correct()}</Button
          >
        {/if}
      </li>
    {/each}
  </ul>
  {#if signedIn && editing == null}
    <Button
      variant="outline"
      size="sm"
      class="mbs-2 text-xs block-8"
      onclick={() => start({ mode: "add" })}>{m.edit_results_add()}</Button
    >
  {/if}

  {#if editing != null}
    <div class="contrib-settle mbs-3 grid gap-2">
      <div class="flex flex-wrap items-center justify-between gap-2">
        {#if editing.mode === "add"}
          <Select.Root
            type="single"
            value={stage}
            onValueChange={(next: string) => {
              const found = stages.find((item) => item === next);
              if (found != null) {
                stage = found;
              }
            }}
          >
            <Select.Trigger
              class="text-sm block-8"
              aria-label={m.edit_results_stage()}
              >{stageLabel[stage]()}</Select.Trigger
            >
            <Select.Content>
              {#each stages as item (item)}
                <Select.Item value={item}>{stageLabel[item]()}</Select.Item>
              {/each}
            </Select.Content>
          </Select.Root>
        {:else}
          <span class="text-xs text-muted-foreground"
            >{m.edit_results_changed({ count: cells.length })}</span
          >
        {/if}
        <Button
          variant="ghost"
          size="sm"
          class="px-2 text-xs text-muted-foreground block-7"
          onclick={stop}>{m.edit_results_discard()}</Button
        >
      </div>
      {#if corrected != null && corrected.pipeline !== "contrib"}
        <p class="text-xs text-pretty text-muted-foreground">
          {m.edit_results_fold({ pipeline: corrected.pipeline })}
        </p>
      {/if}
      {#if loading}
        <p class="text-sm text-muted-foreground" role="status">
          {m.edit_results_loading()}
        </p>
      {:else if failed != null}
        <p class="text-sm" role="alert">{failed}</p>
      {:else}
        {#if loaded != null && loaded.total > loaded.cells.length}
          <p class="text-xs text-pretty text-muted-foreground">
            {m.edit_results_partial({
              shown: count(loaded.cells.length),
              total: count(loaded.total),
            })}
          </p>
        {/if}
        {#if editing.mode === "correct" && loaded != null && loaded.cells.length > shown.length}
          <p class="text-xs text-pretty text-muted-foreground">
            {m.edit_results_hidden({
              count: loaded.cells.length - shown.length,
            })}
          </p>
        {/if}
        <div
          class="overflow-auto rounded-sm border border-border max-block-[60dvh]"
        >
          <table class="font-figures text-sm inline-full">
            <thead class="sticky inset-bs-0 z-10 bg-background">
              <tr class="border-be text-xs text-muted-foreground">
                <th
                  class="sticky inset-s-0 bg-background px-2 py-1.5 text-start font-normal min-inline-36"
                  >{m.edit_results_place()}</th
                >
                {#each columns as column (column.key)}
                  <th
                    class={cn(
                      "px-2 py-1.5 text-end align-bottom font-normal min-inline-22",
                      { "border-s": column.key === firstMeasure },
                    )}>{column.label}</th
                  >
                {/each}
              </tr>
            </thead>
            <tbody>
              {#each rows as row (row.key)}
                {const off = $derived(mismatched.has(row.key))}
                <tr class="border-be align-baseline">
                  <th
                    scope="row"
                    class={cn(
                      "sticky inset-s-0 bg-background px-2 py-1 text-start font-normal",
                      { "shadow-[inset_2px_0_0_var(--foreground)]": off },
                    )}
                  >
                    <span class="block truncate max-inline-40">{row.label}</span
                    >
                    <span class="block text-xs text-muted-foreground"
                      >{#if off}{m.edit_results_row_sum({
                          sum: count(sums.get(row.key) ?? 0),
                        })}{:else}{row.detail}{/if}</span
                    >
                    {#if row.key.startsWith("new:")}
                      <button
                        type="button"
                        class="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                        onclick={() =>
                          (added = added.filter(
                            (item) => item.key !== row.key,
                          ))}>{m.edit_results_remove_place()}</button
                      >
                    {/if}
                  </th>
                  {#each columns as column (column.key)}
                    {const key = $derived(`${row.key}|${column.key}`)}
                    {const held = $derived(original.get(key))}
                    {const raw = $derived(
                      values[key] ?? (held == null ? "" : String(held)),
                    )}
                    {const parsed = $derived(parse(raw))}
                    {const dirty = $derived(
                      values[key] != null && parsed !== (held ?? null),
                    )}
                    <td
                      class={cn("p-0.5", {
                        "border-s": column.key === firstMeasure,
                      })}
                    >
                      <input
                        class={cn(
                          "rounded-xs border border-transparent bg-transparent px-1.5 text-end font-figures tabular-nums block-8 inline-full hover:border-input focus-visible:border-ring focus-visible:outline-none",
                          {
                            "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                              dirty,
                          },
                          { "border-destructive": Number.isNaN(parsed) },
                        )}
                        inputmode="decimal"
                        autocomplete="off"
                        value={raw}
                        aria-label={`${row.label}, ${column.label}`}
                        aria-invalid={Number.isNaN(parsed)}
                        title={dirty && held != null ? count(held) : undefined}
                        oninput={(
                          event: Event & { currentTarget: HTMLInputElement },
                        ) => (values[key] = event.currentTarget.value)}
                      />
                    </td>
                  {/each}
                </tr>
              {/each}
            </tbody>
            <tfoot class="sticky inset-be-0 bg-background">
              <tr class="border-bs-2 border-foreground font-semibold">
                <th
                  scope="row"
                  class="sticky inset-s-0 bg-background px-2 py-1.5 text-start"
                  >{m.edit_results_total()}</th
                >
                {#each columns as column, index (column.key)}
                  <td
                    class={cn("px-2 py-1.5 text-end", {
                      "border-s": column.key === firstMeasure,
                    })}>{count(totals[index] ?? 0)}</td
                  >
                {/each}
              </tr>
            </tfoot>
          </table>
        </div>
        {#if broken}
          <p class="text-xs text-destructive" role="alert">
            {m.edit_results_invalid()}
          </p>
        {/if}
        {#if mismatched.size > 0}
          <p class="text-xs font-semibold" role="status">
            {m.edit_results_mismatch({ count: mismatched.size })}
          </p>
        {/if}
        {#if editing.mode === "add"}
          <form
            class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2"
            onsubmit={(event) => {
              event.preventDefault();
              addPlace();
            }}
          >
            <Input
              bind:value={scheme}
              class="text-sm block-8"
              aria-label={m.edit_results_scheme()}
            />
            <Input
              bind:value={code}
              class="text-sm block-8"
              aria-label={m.edit_results_code()}
            />
            <Button
              type="submit"
              variant="outline"
              size="sm"
              class="text-xs block-8">{m.edit_results_add_place()}</Button
            >
          </form>
        {/if}
      {/if}
    </div>
  {/if}
</section>
