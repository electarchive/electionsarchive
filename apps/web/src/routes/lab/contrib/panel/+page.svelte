<script lang="ts">
  import "#lib/components/contrib/contrib.css";
  import Checks from "#routes/lab/contrib/checks.svelte";
  import Scan from "#routes/lab/contrib/scan.svelte";
  import { resolve } from "$app/paths";
  import History from "@lucide/svelte/icons/history";
  import X from "@lucide/svelte/icons/x";
  import type { Box } from "#routes/lab/contrib/data.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { cn } from "#lib/utils.js";
  import { copy } from "#routes/lab/contrib/copy.js";
  import {
    columns,
    contest,
    integer,
    share,
    surnames,
  } from "#routes/lab/contrib/data.js";
  import {
    cellId,
    parse,
    Returns,
  } from "#routes/lab/contrib/returns.svelte.js";

  const [weller, renner] = contest.candidates;
  const returns = new Returns();

  const systems = [
    { value: "plurality", label: copy.system_plurality },
    { value: "two-round", label: copy.system_two_round },
    { value: "ranked-choice", label: copy.system_ranked_choice },
    { value: "top-two", label: copy.system_top_two },
  ];

  const evidenceItems = [
    {
      value: "p33",
      label: copy.evidence_page,
      detail: copy.evidence_page_detail,
      group: copy.evidence_group_sources,
    },
    {
      value: "link",
      label: copy.evidence_link,
      group: copy.evidence_group_new,
    },
    {
      value: "upload",
      label: copy.evidence_upload,
      group: copy.evidence_group_new,
    },
  ];

  let open = $state(true);
  let sheet = $state<"peek" | "half" | "full">("half");
  let focus = $state<string | null>(null);
  let name = $state<string>(contest.district);
  let system = $state<string | null>("plurality");
  let note = $state<string | null>(copy.panel_note_draft);
  let wikidata = $state<string | null>(null);
  let summary = $state("");
  const evidence = $state<Record<string, string | null>>({});
  let submitted = $state(false);
  let guard = $state(false);

  const changes = $derived.by(() => {
    const out: Array<{
      id: string;
      county: string;
      candidate: string;
      before: number;
      after: string;
      box: Box;
      row: Box;
    }> = [];
    for (const row of returns.rows) {
      for (const column of columns) {
        if (!returns.changed(row, column)) continue;
        const value = parse(row.cells[column]);
        out.push({
          id: cellId(row, column),
          county: row.county.name,
          candidate: surnames[column],
          before: row.county.votes[column],
          after: value == null ? row.cells[column] : integer.format(value),
          box: row.county.boxes[column],
          row: [
            0.03,
            row.county.box[1] - 0.012,
            0.6,
            row.county.box[3] + 0.012,
          ],
        });
      }
    }
    return out;
  });

  const nameChanged = $derived(name.trim() !== contest.district);
  const systemChanged = $derived(system !== "plurality");
  const noteAdded = $derived(note != null && note.trim() !== "");
  const idAdded = $derived(wikidata != null && wikidata.trim() !== "");
  const count = $derived(
    changes.length +
      Number(nameChanged) +
      Number(systemChanged) +
      Number(noteAdded) +
      Number(idAdded),
  );
  const tier = $derived(changes.length > 0 || systemChanged ? 3 : 1);

  const checks = $derived.by(() => {
    const lines: Array<{
      code: string;
      text: string;
      state: "passed" | "finding" | "failed";
      target?: string;
    }> = [
      returns.invalid
        ? {
            code: "values.integer",
            text: copy.check_integer_failed,
            state: "failed",
          }
        : { code: "values.integer", text: copy.check_integer, state: "passed" },
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
              target: changes[0]?.id,
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
    const leads = returns.sums[0] > returns.sums[1];
    lines.push({
      code: "outcomes.winner",
      text: leads ? copy.check_winner : copy.check_winner_off,
      state: leads ? "passed" : "failed",
    });
    lines.push({
      code: "evidence.cited",
      text: copy.check_evidence,
      state: "passed",
    });
    return lines;
  });

  const failed = $derived(
    checks.filter((line) => line.state === "failed").length,
  );
  const findings = $derived(
    checks.filter((line) => line.state === "finding").length,
  );
  const ready = $derived(count > 0 && summary.trim() !== "" && failed === 0);

  function reset() {
    returns.reset();
    name = contest.district;
    system = "plurality";
    note = null;
    wikidata = null;
    summary = "";
    submitted = false;
  }

  function onkeydown(event: KeyboardEvent) {
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement
    )
      return;
    if (event.key === "e" && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      open = true;
    }
    if (event.key === "Escape") open = false;
  }

  function locate(id: string) {
    focus = returns.find(id)?.row.county.id ?? null;
    document.getElementById(`cell-${id}`)?.focus();
  }
</script>

<svelte:window {onkeydown} />

<svelte:head>
  <title>{copy.panel_page_title}</title>
</svelte:head>

<div
  class={cn("contrib px-4 pbs-6 pbe-24 max-sm:px-3", {
    "lg:pe-[max(1rem,calc(436px-(100cqi-80rem)/2))]": open,
  })}
>
  <nav
    class="flex flex-wrap items-baseline gap-x-2 text-sm text-muted-foreground"
    aria-label={copy.breadcrumb}
  >
    <a class="hover:text-foreground" href={resolve("/lab/contrib")}
      >{copy.lab_back}</a
    >
    <span aria-hidden="true">/</span>
    <span>{copy.crumb_us}</span>
    <span aria-hidden="true">/</span>
    <span>{copy.crumb_il}</span>
    <span aria-hidden="true">/</span>
    <span>{copy.crumb_election}</span>
  </nav>

  <header
    class="mbs-4 flex flex-wrap items-end justify-between gap-4 border-be pbe-5"
  >
    <div class="max-inline-3xl">
      <h1 class="title">{copy.panel_contest_title}</h1>
      <p class="mbs-2 text-muted-foreground">{contest.dateLabel}</p>
    </div>
    <Button
      variant={open ? "secondary" : "outline"}
      aria-pressed={open}
      onclick={() => (open = !open)}
    >
      {copy.edit}
      <kbd>E</kbd>
    </Button>
  </header>

  <p class="mbs-5 font-serif text-lg/relaxed text-pretty max-inline-[62ch]">
    {copy.panel_lede}
  </p>

  <section class="mbs-8" aria-labelledby="results">
    <h2 id="results" class="heading">{copy.results}</h2>
    <table class="mbs-3 text-sm inline-full max-inline-2xl">
      <thead class="text-muted-foreground">
        <tr class="border-be">
          <th class="py-1.5 text-start font-normal">{copy.candidate}</th>
          <th class="py-1.5 text-end font-normal">{copy.votes}</th>
          <th class="py-1.5 text-end font-normal inline-20">{copy.share}</th>
        </tr>
      </thead>
      <tbody>
        {#each contest.candidates as candidate (candidate.id)}
          <tr class="border-be">
            <td class="py-2">
              <span class="flex items-center gap-2">
                <span
                  class="rounded-full block-2.5 inline-2.5"
                  style:background={candidate.color}
                ></span>
                <span class="font-semibold">{candidate.name}</span>
                <span class="text-muted-foreground">{candidate.party}</span>
                {#if candidate.incumbent}<span class="text-muted-foreground"
                    >{copy.incumbent}</span
                  >{/if}
              </span>
              <span class="mbs-1.5 block bg-muted block-1">
                <span
                  class="block block-1"
                  style:background={candidate.color}
                  style:inline-size="{share(
                    candidate.total,
                    weller.total + renner.total,
                  )}%"
                ></span>
              </span>
            </td>
            <td class="py-2 text-end font-figures font-semibold"
              >{integer.format(candidate.total)}</td
            >
            <td class="py-2 text-end font-figures"
              >{share(candidate.total, weller.total + renner.total)}%</td
            >
          </tr>
        {/each}
      </tbody>
    </table>
  </section>

  <section class="mbs-10" aria-labelledby="counties">
    <h2 id="counties" class="heading">{copy.by_county}</h2>
    <table class="mbs-3 text-sm inline-full max-inline-2xl">
      <thead class="text-muted-foreground">
        <tr class="border-be">
          <th class="py-1.5 text-start font-normal">{copy.county}</th>
          <th class="py-1.5 text-end font-normal">{surnames[0]}</th>
          <th class="py-1.5 text-end font-normal">{surnames[1]}</th>
          <th class="py-1.5 text-end font-normal max-sm:hidden"
            >{copy.margin}</th
          >
        </tr>
      </thead>
      <tbody>
        {#each returns.rows as row (row.county.id)}
          {const a = $derived(parse(row.cells[0]) ?? 0)}
          {const b = $derived(parse(row.cells[1]) ?? 0)}
          <tr
            class={cn(
              "border-be transition-colors duration-100 motion-reduce:transition-none",
              { highlighter: focus === row.county.id },
            )}
          >
            <td class="py-1.5"
              >{row.county.name}{#if row.county.isPartial}<span
                  class="ms-1 text-muted-foreground">{copy.part}</span
                >{/if}</td
            >
            {#each columns as column (column)}
              {const value = $derived(parse(row.cells[column]))}
              <td class="py-1.5 text-end font-figures">
                {#if returns.changed(row, column)}
                  <Proof
                    class="contrib-settle"
                    before={integer.format(row.county.votes[column])}
                    after={value == null
                      ? row.cells[column]
                      : integer.format(value)}
                    n={1}
                  />
                {:else}
                  {integer.format(row.county.votes[column])}
                {/if}
              </td>
            {/each}
            <td
              class="py-1.5 text-end font-figures text-muted-foreground max-sm:hidden"
            >
              {copy.points(share(Math.abs(a - b), a + b))}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="mbs-3 text-sm text-muted-foreground">{copy.panel_source_line}</p>
  </section>
</div>

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
    aria-labelledby="panel-title"
  >
    <button
      type="button"
      class="grid place-items-center pbs-2 lg:hidden"
      aria-label={copy.sheet_resize}
      onclick={() =>
        (sheet =
          sheet === "half" ? "full" : sheet === "full" ? "peek" : "half")}
    >
      <span class="rounded-full bg-input block-1 inline-10"></span>
    </button>

    <div class="flex items-start justify-between gap-3 border-be px-4 py-3">
      <div class="min-inline-0">
        <h2 id="panel-title" class="text-base font-bold">
          {copy.edit_contest}
        </h2>
        <p class="truncate text-sm text-muted-foreground">
          {copy.panel_contest_title}, {contest.dateLabel}
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label={copy.close}
        onclick={() => (open = false)}><X /></Button
      >
    </div>

    {#if submitted}
      <div class="grid flex-1 content-start gap-4 overflow-y-auto px-4 py-6">
        <p class="text-sm text-muted-foreground">{copy.submitted_label}</p>
        <p class="heading">{copy.submitted_title}</p>
        <p class="font-serif leading-relaxed">{summary}</p>
        <p class="text-sm">
          {tier === 3 ? copy.submitted_tier3 : copy.submitted_tier1}
        </p>
        <div class="flex gap-2">
          <Button href={resolve("/lab/contrib/proposal")}
            >{copy.view_proposal}</Button
          >
          <Button variant="outline" onclick={reset}>{copy.start_another}</Button
          >
        </div>
      </div>
    {:else}
      <div class="flex-1 overflow-y-auto overscroll-contain">
        <div class="border-be px-4 py-2.5 text-sm">
          <button
            type="button"
            class="flex items-baseline justify-between gap-2 text-start inline-full"
            aria-expanded={guard}
            onclick={() => (guard = !guard)}
          >
            <span>{copy.guard}</span>
            <span class="text-muted-foreground"
              >{guard ? copy.hide : copy.show}</span
            >
          </button>
          {#if guard}
            <a
              class="mbs-2 grid grid-cols-[auto_1fr] gap-x-2 rounded-sm p-1 hover:bg-muted"
              href={resolve("/lab/contrib/proposal")}
            >
              <span class="font-figures text-muted-foreground">#1049</span>
              <span
                >{copy.guard_item}<span class="block text-muted-foreground"
                  >{copy.guard_item_meta}</span
                ></span
              >
            </a>
          {/if}
        </div>

        <section class="border-be px-4 py-3" aria-labelledby="sec-contest">
          <h3 id="sec-contest" class="text-sm font-bold">{copy.sec_contest}</h3>
          <dl
            class="mbs-2 grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 text-sm"
          >
            <dt class="pbs-2 text-muted-foreground">{copy.field_name}</dt>
            <dd class="py-1">
              <Input
                bind:value={name}
                class="border-transparent bg-transparent px-1.5 block-8 hover:border-input"
                onfocus={() => (focus = "name")}
                onblur={() => (focus = null)}
              />
              {#if nameChanged}
                <Proof
                  class="ps-1.5 text-xs"
                  before={contest.district}
                  after={name}
                />
              {:else}
                <span
                  class="flex items-center gap-1 ps-1.5 text-xs text-muted-foreground"
                  >{copy.meta_name}<a
                    href={resolve("/lab/contrib")}
                    aria-label={copy.history}
                    ><History class="block-3 inline-3" /></a
                  ></span
                >
              {/if}
            </dd>
            <dt class="pbs-2 text-muted-foreground">{copy.field_date}</dt>
            <dd class="py-1">
              <span class="block px-1.5 py-1 font-figures">{contest.date}</span>
              <span class="ps-1.5 text-xs text-muted-foreground"
                >{copy.meta_date}</span
              >
            </dd>
            <dt class="pbs-2 text-muted-foreground">{copy.field_system}</dt>
            <dd class="py-1">
              <Combobox
                items={systems}
                bind:value={system}
                label={copy.field_system}
                placeholder={copy.system_placeholder}
                search={copy.system_search}
                empty={copy.no_match}
              />
              {#if systemChanged}
                <Proof
                  class="ps-1.5 text-xs"
                  before={copy.system_plurality}
                  after={systems.find((item) => item.value === system)?.label ??
                    ""}
                />
              {/if}
            </dd>
          </dl>
        </section>

        <section class="border-be px-4 py-3" aria-labelledby="sec-results">
          <div class="flex items-baseline justify-between">
            <h3 id="sec-results" class="text-sm font-bold">
              {copy.sec_results}
            </h3>
            <span class="text-xs text-muted-foreground"
              >{copy.meta_results}</span
            >
          </div>
          <table class="mbs-2 text-sm inline-full">
            <thead class="text-muted-foreground">
              <tr class="border-be">
                <th class="py-1 text-start font-normal">{copy.county}</th>
                <th class="py-1 pe-1.5 text-end font-normal">{surnames[0]}</th>
                <th class="py-1 pe-1.5 text-end font-normal">{surnames[1]}</th>
              </tr>
            </thead>
            <tbody>
              {#each returns.rows as row (row.county.id)}
                <tr
                  class={cn("border-be", {
                    "bg-muted": focus === row.county.id,
                  })}
                >
                  <td class="truncate py-0.5 pe-2">{row.county.name}</td>
                  {#each columns as column (column)}
                    {const changed = $derived(returns.changed(row, column))}
                    <td class="py-0.5 inline-26">
                      <input
                        id="cell-{cellId(row, column)}"
                        class={cn(
                          "field rounded-[2px] border-transparent bg-transparent px-1.5 text-end font-figures block-7 inline-full hover:border-input",
                          {
                            "font-semibold shadow-[inset_0_-2px_0_var(--foreground)]":
                              changed,
                          },
                        )}
                        inputmode="numeric"
                        aria-label="{row.county.name}, {contest.candidates[
                          column
                        ].name}"
                        aria-invalid={parse(row.cells[column]) == null}
                        bind:value={row.cells[column]}
                        onfocus={() => (focus = row.county.id)}
                        onblur={() => {
                          focus = null;
                          returns.tidy(row, column);
                        }}
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
                    class={cn("py-1.5 pe-1.5 text-end font-figures", {
                      "font-bold": returns.off[column],
                    })}>{integer.format(returns.sums[column])}</td
                  >
                {/each}
              </tr>
              <tr>
                <td class="text-xs text-muted-foreground">{copy.printed}</td>
                {#each contest.candidates as candidate (candidate.id)}
                  <td
                    class="pe-1.5 text-end font-figures text-xs text-muted-foreground"
                    >{integer.format(candidate.total)}</td
                  >
                {/each}
              </tr>
            </tfoot>
          </table>

          {#if changes.length > 0}
            <ol class="mbs-3 grid gap-3 border-s-2 border-foreground ps-3">
              {#each changes as change (change.id)}
                <li class="grid gap-2">
                  <div
                    class="flex flex-wrap items-baseline justify-between gap-x-3 text-sm"
                  >
                    <span>{change.county}, {change.candidate}</span>
                    <Proof
                      before={integer.format(change.before)}
                      after={change.after}
                      n={1}
                    />
                  </div>
                  <Scan
                    region={change.row}
                    marks={[
                      { id: change.id, box: change.box, label: change.county },
                    ]}
                    selected={change.id}
                    alt={copy.crop_alt(change.county)}
                    class="rounded-[2px] border"
                  />
                  <Combobox
                    items={evidenceItems}
                    bind:value={
                      () => evidence[change.id] ?? "p33",
                      (v: string | null) => (evidence[change.id] = v)
                    }
                    label={copy.evidence}
                    placeholder={copy.evidence_placeholder}
                    search={copy.evidence_search}
                    empty={copy.no_match}
                  />
                </li>
              {/each}
            </ol>
          {:else}
            <p class="mbs-2 text-xs text-muted-foreground">
              {copy.results_hint}
            </p>
          {/if}
        </section>

        <section class="border-be px-4 py-3" aria-labelledby="sec-notes">
          <div class="flex items-baseline justify-between">
            <h3 id="sec-notes" class="text-sm font-bold">{copy.sec_notes}</h3>
            {#if note == null}
              <Button variant="link" size="xs" onclick={() => (note = "")}
                >{copy.add_note}</Button
              >
            {:else}
              <Button variant="link" size="xs" onclick={() => (note = null)}
                >{copy.discard}</Button
              >
            {/if}
          </div>
          {#if note != null}
            <div class="mbs-2 border-s-2 border-foreground ps-3">
              <Textarea
                bind:value={note}
                rows={3}
                class="text-sm"
                aria-label={copy.new_note}
                onfocus={() => (focus = "note")}
                onblur={() => (focus = null)}
              />
              <p class="mbs-1 text-xs text-muted-foreground">
                {copy.note_hint}
              </p>
            </div>
          {:else}
            <p class="mbs-1 text-sm text-muted-foreground">{copy.no_notes}</p>
          {/if}
        </section>

        <section class="border-be px-4 py-3" aria-labelledby="sec-sources">
          <h3 id="sec-sources" class="text-sm font-bold">{copy.sec_sources}</h3>
          <p class="mbs-2 text-sm">{contest.sourceTitle}</p>
          <p class="text-xs text-muted-foreground">{copy.source_meta}</p>
        </section>

        <section class="px-4 py-3" aria-labelledby="sec-ids">
          <h3 id="sec-ids" class="text-sm font-bold">{copy.sec_ids}</h3>
          <div
            class="mbs-2 grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-x-3 text-sm"
          >
            <span class="text-muted-foreground">{copy.wikidata}</span>
            {#if wikidata == null}
              <Button
                variant="link"
                size="xs"
                class="justify-self-start px-0"
                onclick={() => (wikidata = "")}>{copy.add_id}</Button
              >
            {:else}
              <div class="border-s-2 border-foreground ps-2">
                <Input
                  bind:value={wikidata}
                  placeholder="Q…"
                  class="font-figures block-8"
                  aria-label={copy.wikidata}
                />
              </div>
            {/if}
          </div>
        </section>
      </div>

      <footer class="grid gap-2.5 border-bs px-4 py-3">
        <Input
          bind:value={summary}
          placeholder={copy.summary_placeholder}
          aria-label={copy.summary}
          aria-required="true"
        />
        <div class="text-xs">
          <p>
            <span class="font-semibold">{copy.change_count(count)}</span>
            {tier === 3 ? copy.tier3_line : copy.tier1_line}
          </p>
          <p
            class={cn("text-muted-foreground", {
              "font-semibold text-foreground": failed + findings > 0,
            })}
          >
            {copy.check_line(
              checks.length - failed - findings,
              findings,
              failed,
            )}
          </p>
        </div>
        <details class="text-sm">
          <summary
            class="cursor-pointer text-xs text-muted-foreground hover:text-foreground"
            >{copy.show_checks}</summary
          >
          <div class="mbs-1"><Checks lines={checks} onfocus={locate} /></div>
        </details>
        <div class="flex items-center justify-between gap-2">
          <Button variant="outline" href={resolve("/lab/contrib/workspace")}
            >{copy.open_workspace}</Button
          >
          <Button disabled={!ready} onclick={() => (submitted = true)}
            >{copy.submit}</Button
          >
        </div>
      </footer>
    {/if}
  </aside>
{/if}
