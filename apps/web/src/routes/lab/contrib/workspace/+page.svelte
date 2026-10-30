<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import Checks from "#routes/lab/contrib/checks.svelte";
  import Proof from "#lib/components/contrib/proof.svelte";
  import Scan from "#routes/lab/contrib/scan.svelte";
  import { resolve } from "$app/paths";
  import type { Box, Column } from "#routes/lab/contrib/data.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Command from "#lib/components/ui/command/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { cn } from "#lib/utils.js";
  import { copy } from "#routes/lab/contrib/copy.js";
  import {
    columns,
    contest,
    integer,
    page,
    surnames,
  } from "#routes/lab/contrib/data.js";
  import {
    cellId,
    parse,
    Returns,
  } from "#routes/lab/contrib/returns.svelte.js";

  const region: Box = [0.02, 0.435, 0.98, 0.745];
  const returns = new Returns((county, column) =>
    county.id === "kankakee" && column === 0 ? 26_593 : county.votes[column],
  );
  const tabs = [
    ["diff", copy.ws_tab_diff],
    ["checks", copy.ws_tab_checks],
    ["evidence", copy.ws_tab_evidence],
  ] as const;

  let selected = $state<string | null>("kankakee-0");
  let tab = $state<"diff" | "checks" | "evidence">("checks");
  let palette = $state(false);
  let summary = $state(copy.ws_summary_draft);
  let submitted = $state(false);

  const suspect = $derived.by(() => {
    const ids: Array<string> = [];
    for (const column of columns) {
      if (!returns.off[column]) continue;
      for (const row of returns.rows) {
        if (returns.changed(row, column)) ids.push(cellId(row, column));
      }
    }
    return ids;
  });

  const offColumn = $derived(columns.find((column) => returns.off[column]));

  const checks = $derived.by(() => {
    const lines: Array<{
      code: string;
      text: string;
      state: "passed" | "finding" | "failed";
      target?: string;
    }> = [
      {
        code: "values.integer",
        text: returns.invalid ? copy.check_integer_failed : copy.check_integer,
        state: returns.invalid ? "failed" : "passed",
      },
    ];
    for (const column of columns) {
      const candidate = contest.candidates[column];
      const code = `accounting.column.${candidate.abbr.toLowerCase()}`;
      lines.push(
        returns.off[column]
          ? {
              code,
              text: copy.check_column_off(
                surnames[column],
                integer.format(returns.sums[column]),
                integer.format(candidate.total),
              ),
              state: "finding",
              target: suspect[0],
            }
          : {
              code,
              text: copy.check_column(
                surnames[column],
                integer.format(candidate.total),
              ),
              state: "passed",
            },
      );
    }
    lines.push({
      code: "outcomes.winner",
      text: copy.check_winner,
      state: returns.sums[0] > returns.sums[1] ? "passed" : "failed",
    });
    lines.push({
      code: "geography.units",
      text: copy.ws_check_units,
      state: "passed",
    });
    lines.push({
      code: "evidence.cited",
      text: copy.ws_check_evidence,
      state: "passed",
    });
    lines.push({
      code: "scope.owner",
      text: copy.ws_check_owner,
      state: "passed",
    });
    return lines;
  });

  const findings = $derived(
    checks.filter((line) => line.state !== "passed").length,
  );

  const marks = $derived([
    ...returns.rows.flatMap((row) =>
      columns.map((column) => ({
        id: cellId(row, column),
        box: row.county.boxes[column],
        label: `${row.county.name}, ${contest.candidates[column].name}`,
        state: suspect.includes(cellId(row, column))
          ? ("finding" as const)
          : ("linked" as const),
      })),
    ),
    ...contest.candidates.map((candidate) => ({
      id: `total-${candidate.id}`,
      box: candidate.totalBox,
      label: copy.ws_printed_total(candidate.name),
      state: "total" as const,
    })),
  ]);

  const current = $derived(selected == null ? null : returns.find(selected));

  function pick(id: string) {
    selected = id;
    document.getElementById(`ws-${id}`)?.focus();
  }

  function run() {
    tab = "checks";
  }

  function move(event: KeyboardEvent, index: number, column: Column) {
    const step =
      event.key === "Enter" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowUp"
          ? -1
          : 0;
    const target = returns.rows[index + step];
    if (step === 0 || target == null) return;
    event.preventDefault();
    pick(cellId(target, column));
  }

  function onkeydowncapture(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      event.stopPropagation();
      palette = !palette;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      run();
    }
  }

  function act(action: () => void) {
    palette = false;
    action();
  }
</script>

<svelte:window {onkeydowncapture} />

<svelte:head>
  <title>{copy.ws_page_title}</title>
</svelte:head>

<div
  class="contrib fixed inset-0 z-50 grid grid-rows-[auto_minmax(0,1fr)_auto] bg-background text-sm"
>
  <header
    class="flex flex-wrap items-center gap-x-4 gap-y-1 border-be px-4 py-2"
  >
    <a
      class="text-muted-foreground hover:text-foreground"
      href={resolve("/lab/contrib")}>{copy.lab_back}</a
    >
    <h1 class="flex items-baseline gap-2 font-bold min-inline-0">
      <span class="shrink-0 font-figures text-muted-foreground"
        >{copy.ws_draft_number}</span
      >
      <span class="truncate">{copy.ws_title}</span>
    </h1>
    <p class="ms-auto flex items-baseline gap-4 text-muted-foreground">
      <span>{copy.ws_saved}</span>
      <span>{copy.ws_tier}</span>
      <button
        type="button"
        class={cn("hover:text-foreground", {
          "font-semibold text-foreground": findings > 0,
        })}
        onclick={() => (tab = "checks")}>{copy.ws_findings(findings)}</button
      >
    </p>
  </header>

  <div
    class="grid grid-cols-1 overflow-y-auto min-block-0 lg:grid-cols-[15rem_minmax(0,1fr)_22rem] lg:overflow-hidden"
  >
    <nav
      class="border-be lg:overflow-y-auto lg:border-e lg:border-be-0 max-lg:hidden"
      aria-label={copy.ws_outline}
    >
      <h2 class="px-4 pbs-3 pbe-1 text-xs text-muted-foreground">
        {copy.ws_outline}
      </h2>
      <ul class="grid px-2 pbe-3">
        <li class="px-2 py-1.5 font-semibold">{copy.ws_outline_election}</li>
        <li>
          <button
            type="button"
            class="flex items-baseline justify-between rounded-sm bg-muted px-2 py-1.5 ps-5 text-start inline-full"
          >
            <span>{copy.ws_outline_contest}</span>
            {#if findings > 0}<span class="font-figures font-semibold"
                >{findings}</span
              >{/if}
          </button>
        </li>
        <li class="px-2 py-1.5 ps-8 text-muted-foreground">
          {copy.ws_outline_set}
        </li>
        <li class="px-2 py-1.5 ps-8 text-muted-foreground">
          {copy.ws_outline_values}
        </li>
        <li class="mbs-3 px-2 py-1.5 font-semibold">
          {copy.ws_outline_evidence}
        </li>
        <li class="px-2 py-1.5 ps-5">
          <span class="block truncate">{copy.ws_file}</span>
          <span class="text-xs text-muted-foreground">{copy.ws_file_meta}</span>
        </li>
      </ul>
    </nav>

    <main
      class="grid gap-px bg-border lg:overflow-y-auto lg:min-block-0 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]"
    >
      <section class="bg-background p-4" aria-labelledby="ws-scan">
        <div class="flex items-baseline justify-between pbe-2">
          <h2 id="ws-scan" class="font-semibold">
            {copy.ws_scan_title(page.number)}
          </h2>
          <span class="text-xs text-muted-foreground">{copy.ws_scan_hint}</span>
        </div>
        <Scan
          {region}
          {marks}
          {selected}
          alt={copy.ws_scan_alt}
          class="rounded-[2px] border xl:sticky xl:inset-bs-4"
          onselect={pick}
        />
      </section>

      <section class="bg-background p-4" aria-labelledby="ws-grid">
        <div class="flex items-baseline justify-between pbe-2">
          <h2 id="ws-grid" class="font-semibold">{copy.ws_grid_title}</h2>
          <span class="text-xs text-muted-foreground">{copy.ws_grid_hint}</span>
        </div>
        <table class="inline-full">
          <thead class="text-muted-foreground">
            <tr class="border-be">
              <th class="py-1 text-start font-normal">{copy.county}</th>
              {#each surnames as name (name)}
                <th class="py-1 pe-2 text-end font-normal">{name}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each returns.rows as row, i (row.county.id)}
              <tr class={cn("border-be", { "bg-muted": current?.row === row })}>
                <td class="py-0.5 pe-2">
                  {row.county.name}{#if row.county.isPartial}<span
                      class="ms-1 text-muted-foreground">{copy.part}</span
                    >{/if}
                </td>
                {#each columns as column (column)}
                  {const id = $derived(cellId(row, column))}
                  {const flagged = $derived(suspect.includes(id))}
                  <td class="py-0.5 inline-28">
                    <input
                      id="ws-{id}"
                      class={cn(
                        "field rounded-[2px] bg-transparent px-2 text-end font-figures block-7 inline-full",
                        {
                          "border-transparent hover:border-input":
                            !flagged && selected !== id,
                          "border-2 border-dashed border-foreground font-semibold":
                            flagged,
                        },
                      )}
                      inputmode="numeric"
                      aria-label="{row.county.name}, {contest.candidates[column]
                        .name}"
                      aria-invalid={parse(row.cells[column]) == null}
                      aria-describedby={flagged ? "ws-finding" : undefined}
                      bind:value={row.cells[column]}
                      onfocus={() => (selected = id)}
                      onblur={() => returns.tidy(row, column)}
                      onkeydown={(event) => move(event, i, column)}
                    />
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
          <tfoot>
            <tr>
              <td class="py-1.5 text-muted-foreground">{copy.sum}</td>
              {#each columns as column (column)}
                <td
                  class={cn("py-1.5 pe-2 text-end font-figures", {
                    "font-bold": returns.off[column],
                  })}>{integer.format(returns.sums[column])}</td
                >
              {/each}
            </tr>
            <tr>
              <td class="text-xs text-muted-foreground">{copy.printed}</td>
              {#each contest.candidates as candidate (candidate.id)}
                <td class="pe-2 text-end font-figures text-xs">
                  <button
                    type="button"
                    class="text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
                    onclick={() => (selected = `total-${candidate.id}`)}
                    >{integer.format(candidate.total)}</button
                  >
                </td>
              {/each}
            </tr>
          </tfoot>
        </table>
        {#if offColumn != null}
          <p
            id="ws-finding"
            class="mbs-3 border-s-2 border-foreground ps-3 font-semibold"
          >
            {copy.ws_finding_line(
              surnames[offColumn],
              integer.format(returns.sums[offColumn]),
              integer.format(contest.candidates[offColumn].total),
            )}
          </p>
        {/if}
      </section>
    </main>

    <aside
      class="grid grid-rows-[auto_minmax(0,1fr)] border-bs lg:border-s lg:border-bs-0 lg:min-block-0"
      aria-label={copy.ws_inspector}
    >
      <div class="flex gap-1 border-be px-3 pbs-2" role="tablist">
        {#each tabs as [key, label] (key)}
          <button
            type="button"
            role="tab"
            aria-selected={tab === key}
            class={cn("px-2 py-1.5", {
              "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                tab === key,
              "text-muted-foreground hover:text-foreground": tab !== key,
            })}
            onclick={() => (tab = key)}
            >{label}{#if key === "checks" && findings > 0}<span
                class="ms-1 font-figures">{findings}</span
              >{/if}</button
          >
        {/each}
      </div>
      <div class="overflow-y-auto p-4" role="tabpanel">
        {#if tab === "diff"}
          <p class="text-muted-foreground">{copy.ws_diff_summary}</p>
          <ul class="mbs-3 grid border-s-2 border-foreground">
            {#each returns.rows as row (row.county.id)}
              <li
                class={cn(
                  "grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 border-be py-1.5 ps-3",
                  { "bg-muted": current?.row === row },
                )}
              >
                <span class="truncate">{row.county.name}</span>
                {#each columns as column (column)}
                  <button
                    type="button"
                    class="text-end"
                    onclick={() => pick(cellId(row, column))}
                  >
                    <Proof before={null} after={row.cells[column]} n={1} />
                  </button>
                {/each}
              </li>
            {/each}
          </ul>
        {:else if tab === "checks"}
          <p class="pbe-1 text-xs text-muted-foreground">
            {copy.ws_checked_now}
          </p>
          <Checks lines={checks} onfocus={pick} />
        {:else}
          <p class="font-semibold">{copy.evidence_page}</p>
          <p class="text-xs text-muted-foreground">{copy.ws_evidence_meta}</p>
          {#if current != null}
            {const county = $derived(current.row.county)}
            {const box = $derived(county.boxes[current.column])}
            <p class="mbs-4 text-xs text-muted-foreground">
              {copy.ws_evidence_cell(
                county.name,
                contest.candidates[current.column].name,
              )}
            </p>
            <Scan
              region={[0.02, county.box[1] - 0.02, 0.62, county.box[3] + 0.02]}
              marks={[{ id: "cell", box, label: county.name }]}
              selected="cell"
              alt={copy.crop_alt(county.name)}
              class="mbs-2 rounded-[2px] border"
            />
            <p class="mbs-2 font-figures text-xs text-muted-foreground">
              {copy.ws_box(box.map((n) => n.toFixed(3)).join(", "))}
            </p>
          {/if}
        {/if}
      </div>
    </aside>
  </div>

  <footer class="flex flex-wrap items-center gap-3 border-bs px-4 py-2.5">
    <Button variant="ghost" size="sm" onclick={() => (palette = true)}
      >{copy.ws_commands} <kbd>⌘K</kbd></Button
    >
    <Button variant="outline" size="sm" onclick={run}
      >{copy.ws_run_checks} <kbd>⌘↵</kbd></Button
    >
    <Input
      bind:value={summary}
      class="flex-1 basis-64 block-8 min-inline-0"
      aria-label={copy.summary}
      placeholder={copy.summary_placeholder}
    />
    {#if submitted}
      <a
        class="font-semibold underline underline-offset-4"
        href={resolve("/lab/contrib/proposal")}>{copy.ws_submitted}</a
      >
    {:else}
      <Button
        size="sm"
        disabled={findings > 0 || summary.trim() === ""}
        onclick={() => (submitted = true)}>{copy.ws_submit}</Button
      >
    {/if}
  </footer>
</div>

<Command.Dialog
  bind:open={palette}
  title={copy.ws_commands}
  description={copy.ws_commands_hint}
  class="sm:max-inline-lg"
>
  <Command.Input placeholder={copy.ws_commands_hint} />
  <Command.List>
    <Command.Empty>{copy.no_match}</Command.Empty>
    <Command.Group heading={copy.ws_group_draft}>
      <Command.Item onSelect={() => act(run)}
        >{copy.ws_run_checks}<Command.Shortcut>⌘↵</Command.Shortcut
        ></Command.Item
      >
      {#if suspect.length > 0}
        <Command.Item onSelect={() => act(() => pick(suspect[0] ?? ""))}
          >{copy.ws_jump_finding}</Command.Item
        >
      {/if}
      <Command.Item onSelect={() => act(() => (tab = "diff"))}
        >{copy.ws_show_diff}</Command.Item
      >
      <Command.Item onSelect={() => act(() => (tab = "evidence"))}
        >{copy.ws_show_evidence}</Command.Item
      >
      <Command.Item
        disabled={findings > 0}
        onSelect={() => act(() => (submitted = true))}
        >{copy.ws_submit}</Command.Item
      >
    </Command.Group>
    <Command.Group heading={copy.ws_group_go}>
      <Command.LinkItem href={resolve("/lab/contrib/proposal")}
        >{copy.ws_go_proposal}</Command.LinkItem
      >
      <Command.LinkItem href={resolve("/lab/contrib/panel")}
        >{copy.ws_go_panel}</Command.LinkItem
      >
    </Command.Group>
  </Command.List>
</Command.Dialog>
