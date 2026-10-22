<script lang="ts">
  import { resolve } from "$app/paths";
  import CheckIcon from "@lucide/svelte/icons/check";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteSet } from "svelte/reactivity";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import Self from "./rows.svelte";

  type Row = {
    path: string;
    label: string;
    kind: string;
    expected: number | null;
    held: number;
    governments: number | null;
    missing: number | null;
    county: number | null;
    precinct: number | null;
    shapes: number | null;
    precincts: number;
    shaped: number;
    countyTotal: number | null;
    precinctTotal: number | null;
    shapesTotal: number | null;
    turnout: number | null;
    turnoutTotal: number | null;
    hasChildren: boolean;
    href: string | null;
    detail: string | null;
  };

  const {
    rows = null,
    parent = null,
    depth = 0,
    view,
    hideDone,
    local,
  }: {
    rows?: Array<Row> | null;
    parent?: string | null;
    depth?: number;
    view: "percent" | "left" | "done";
    hideDone: boolean;
    local: boolean;
  } = $props();

  const LABELS: Record<string, () => string> = {
    federal: m.coverage_federal,
    national: m.coverage_national,
    president: m.coverage_level_president,
    us_senate: m.coverage_level_us_senate,
    us_house: m.coverage_level_us_house,
    governor: m.coverage_level_governor,
    statewide_exec: m.coverage_level_statewide_exec,
    judicial: m.coverage_level_judicial,
    state_upper: m.coverage_level_state_upper,
    state_lower: m.coverage_level_state_lower,
    statewide_measure: m.coverage_level_statewide_measure,
    general: m.coverage_stage_general,
    primary: m.coverage_stage_primary,
    runoff: m.coverage_stage_runoff,
    special: m.coverage_stage_special,
    county: m.coverage_group_county,
    municipality: m.coverage_group_municipality,
    township: m.coverage_group_township,
    school: m.coverage_group_school,
    other: m.coverage_group_other,
  };

  const children = createQuery(() => ({
    ...orpc.coverage.rows.queryOptions({
      input: { parent: parent ?? "", scope: local ? "all" : "nonlocal" },
    }),
    enabled: rows == null && parent != null,
  }));

  const all = $derived(rows ?? children.data?.rows ?? []);
  const list = $derived(
    hideDone
      ? all.filter((row) =>
          [row.missing, row.county, row.precinct, row.shapes, row.turnout].some(
            (gap) => gap != null && gap > 0,
          ),
        )
      : all,
  );
  const limit = $derived(depth === 0 ? 13 : 20);
  let isAll = $state(false);
  const opened = new SvelteSet<string>();
  const current = new Date().getUTCFullYear();
  const compact = new Intl.NumberFormat(getLocale(), {
    notation: "compact",
    maximumFractionDigits: 1,
  });

  const name = (row: Row) =>
    row.kind === "governments" && row.label === "special"
      ? m.coverage_group_special()
      : row.kind === "state" && row.label !== "national"
        ? row.label
        : (LABELS[row.label]?.() ?? row.label);
</script>

{#snippet marker(gap: number | null, total: number | null)}
  <span role="gridcell" class="grid place-items-center">
    {#if gap == null}
      <span class="text-muted-foreground" title={m.coverage_na()}>
        <span aria-hidden="true">–</span>
        <span class="sr-only">{m.coverage_na()}</span>
      </span>
    {:else if gap <= 0}
      <span
        class="grid place-items-center rounded-[2px] bg-(--yes) text-background block-4.5 inline-4.5"
        title={m.coverage_ok()}
      >
        <CheckIcon class="block-3.5 inline-3.5" strokeWidth={3} />
        <span class="sr-only">{m.coverage_ok()}</span>
      </span>
    {:else if total == null || total <= 1}
      <span
        class="rounded-[2px] border border-done-low block-4.5 inline-4.5"
        title={m.coverage_missing()}
      >
        <span class="sr-only">{m.coverage_missing()}</span>
      </span>
    {:else}
      {const done = $derived(Math.max(0, total - gap))}
      {const share = $derived(Math.min(99, Math.floor((done / total) * 100)))}
      {const title = $derived(
        view === "left"
          ? m.coverage_gap({ count: count(gap) })
          : view === "percent"
            ? m.coverage_percent_title({ percent: `${share}%` })
            : m.coverage_done_title({ done: count(done), total: count(total) }),
      )}
      <span
        class={cn(
          "rounded-[2px] border px-1 font-figures text-xs/4 whitespace-nowrap",
          {
            "border-done-low text-done-low": share < 25,
            "border-done-some text-done-some": share >= 25 && share < 50,
            "border-done-half text-done-half": share >= 50 && share < 75,
            "border-done-most text-done-most": share >= 75,
          },
        )}
        {title}
      >
        <span aria-hidden="true">
          {#if view === "left"}
            {count(gap)}
          {:else if view === "percent"}
            {share}%
          {:else}
            <span class="max-sm:grid max-sm:leading-3.5"
              >{compact.format(done)}<span class="max-sm:hidden">/</span><span
                >{compact.format(total)}</span
              ></span
            >
          {/if}
        </span>
        <span class="sr-only">{title}</span>
      </span>
    {/if}
  </span>
{/snippet}

{#if rows == null && children.isPending}
  <div
    role="row"
    class="bg-card py-1.5 text-sm text-muted-foreground"
    style:padding-inline-start="{0.875 + depth * 1.125}rem"
  >
    {m.coverage_loading()}
  </div>
{:else if list.length === 0}
  <div
    role="row"
    class="bg-card py-1.5 text-sm text-muted-foreground"
    style:padding-inline-start="{0.875 + depth * 1.125}rem"
  >
    {all.length === 0 ? m.coverage_empty() : m.coverage_all_done()}
  </div>
{/if}
{#each isAll ? list : list.slice(0, limit) as row (row.path)}
  {const isOpen = $derived(opened.has(row.path))}
  {const label = $derived(name(row))}
  {const isYear = $derived(row.kind === "year")}
  <div
    role="row"
    aria-level={depth + 1}
    aria-expanded={row.hasChildren ? isOpen : undefined}
    class={cn(
      "grid grid-cols-[minmax(0,1fr)_8rem_4.5rem_repeat(5,4.75rem)] items-center gap-x-1 bg-card pe-2",
      {
        "max-sm:grid-cols-[minmax(0,1fr)_repeat(5,2.25rem)]": view !== "done",
        "max-sm:grid-cols-[minmax(0,1fr)_repeat(5,3rem)]": view === "done",
      },
      { "block-10": isYear, "block-8": !isYear },
      { "hover:bg-secondary": row.hasChildren },
    )}
  >
    <span
      role="gridcell"
      class="flex items-center gap-1.5 min-inline-0"
      style:padding-inline-start="{0.5 + depth * 1.125}rem"
    >
      {#if row.hasChildren}
        <button
          type="button"
          class="flex items-center gap-1.5 rounded-sm text-start min-inline-0 focus-visible:outline-2 focus-visible:outline-ring"
          aria-label={isOpen
            ? m.coverage_collapse({ name: label })
            : m.coverage_expand({ name: label })}
          onclick={() => {
            if (isOpen) {
              opened.delete(row.path);
            } else {
              opened.add(row.path);
            }
          }}
        >
          <ChevronRightIcon
            class={cn(
              "shrink-0 text-muted-foreground transition-transform block-4 inline-4 motion-reduce:transition-none",
              { "rotate-90": isOpen },
            )}
          />
          <span
            class={cn("truncate", {
              "font-figures text-lg font-[720]": isYear,
              "font-semibold": row.kind === "state" || row.kind === "federal",
            })}>{label}</span
          >
        </button>
        {#if isYear && Number(row.label) === current}
          <span class="text-xs text-muted-foreground"
            >{m.coverage_in_progress()}</span
          >
        {/if}
      {:else}
        <span class="shrink-0 inline-4"></span>
        {#if row.href == null}
          <span
            class={cn("truncate", { "font-semibold": row.kind === "total" })}
            >{label}</span
          >
        {:else}
          <a
            href={resolve("/[...path]", { path: row.href })}
            class="truncate underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >{label}</a
          >
        {/if}
        {#if row.detail != null}
          <span class="truncate text-xs text-muted-foreground"
            >{row.detail}</span
          >
        {/if}
      {/if}
    </span>
    <span
      role="gridcell"
      class="text-end font-figures text-sm whitespace-nowrap text-muted-foreground max-sm:hidden"
    >
      {#if row.kind === "government"}
        {row.expected === 0 && row.held === 0
          ? m.coverage_not_due()
          : count(row.held)}
      {:else if row.expected != null}
        {m.coverage_held_contests({
          held: count(row.expected - (row.missing ?? 0)),
          expected: count(row.expected),
        })}
      {:else}
        –
      {/if}
    </span>
    <span
      role="gridcell"
      class="text-end font-figures text-sm whitespace-nowrap text-muted-foreground max-sm:hidden"
      title={m.coverage_extra_title()}
    >
      {#if row.kind === "government"}
        {count(Math.max(0, row.held - (row.expected ?? 0)))}
      {:else}
        {count(
          row.expected == null
            ? row.held
            : row.held - row.expected + (row.missing ?? 0),
        )}
      {/if}
    </span>
    {@render marker(row.missing, row.expected)}
    {@render marker(row.county, row.countyTotal)}
    {@render marker(row.precinct, row.precinctTotal)}
    {@render marker(row.shapes, row.shapesTotal)}
    {@render marker(row.turnout, row.turnoutTotal)}
  </div>
  {#if isOpen}
    <Self parent={row.path} depth={depth + 1} {view} {hideDone} {local} />
  {/if}
{/each}
{#if !isAll && list.length > limit}
  <div
    role="row"
    class="bg-card pe-2 pbs-1.5 pbe-2"
    style:padding-inline-start="{0.5 + depth * 1.125}rem"
  >
    <span role="gridcell" class="grid">
      <button
        type="button"
        class="flex items-center justify-center rounded-sm border border-input bg-card text-[0.8125rem] font-semibold keycap block-8 hover:bg-secondary"
        onclick={() => (isAll = true)}
        >{m.places_show_all({ count: count(list.length) })}</button
      >
    </span>
  </div>
{/if}
