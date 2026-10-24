<script lang="ts">
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import Download from "#lib/components/download.svelte";
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, credit, percent, points, precision } from "#lib/format.js";
  import { explain, LOST, OUTCOMES } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { provenance } from "#lib/provenance.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    rows,
    span,
    votes,
    seats,
    trace = null,
  }: {
    rows: Extract<PageData, { kind: "contest" }>["contest"]["rows"];
    span: {
      from: string;
      to: string;
      forward: boolean;
      race: string | null;
    } | null;
    votes: string;
    seats: string | null;
    trace?: string | null;
  } = $props();

  let expanded = $state(false);
  const isQuestion = $derived(
    rows.some((row) => row.key.startsWith("answer:")),
  );

  const here = $derived(span?.forward === true ? span.from : span?.to);
  const there = $derived(span?.forward === true ? span.to : span?.from);
  const lines = $derived(
    rows.map((row) => {
      const before = span?.forward === true ? row.current : row.earlier;
      const after = span?.forward === true ? row.earlier : row.current;
      return {
        row,
        before,
        after,
        change:
          before == null || after == null ? null : after.share - before.share,
        gained:
          before?.seats == null || after?.seats == null
            ? null
            : after.seats - before.seats,
      };
    }),
  );
  const paired = $derived(
    lines.some(
      (line) => line.change != null && !line.row.key.startsWith("kind:"),
    ),
  );
  const digits = $derived.by(() => {
    const [first = 0, second = 0] = rows
      .filter((row) => !row.key.startsWith("kind:"))
      .map((row) => row.current?.share ?? 0)
      .toSorted((a, b) => b - a);
    return second === 0 ? 1 : precision(first - second);
  });
  const topShare = $derived(
    Math.max(0.0001, ...rows.map((row) => row.current?.share ?? 0)),
  );
  const scale = $derived(
    Math.max(
      0.1,
      Math.ceil(
        Math.max(
          ...rows.flatMap((row) => [
            row.current?.share ?? 0,
            row.earlier?.share ?? 0,
          ]),
        ) * 10,
      ) / 10,
    ),
  );
  const step = $derived(scale > 0.5 ? 0.2 : 0.1);
  const ticks = $derived(
    Array.from(
      { length: Math.floor(scale / step + 1e-9) + 1 },
      (_, index) => index * step,
    ),
  );
  const whole = $derived(
    new Intl.NumberFormat(getLocale(), {
      style: "percent",
      maximumFractionDigits: 0,
    }),
  );
  const folded = $derived.by(() => {
    const tail = rows.filter(
      (row, index) =>
        index >= 5 &&
        (row.current?.share ?? 0) < 0.01 &&
        (row.earlier?.share ?? 0) < 0.01 &&
        row.current?.outcome == null &&
        row.current?.seats == null,
    );
    return new Set(tail.length > 2 ? tail.map((row) => row.key) : []);
  });
  const shown = $derived(
    expanded ? lines : lines.filter((line) => !folded.has(line.row.key)),
  );
  const credits = $derived(
    shown.flatMap(({ row }) =>
      row.photo == null
        ? []
        : [{ key: row.key, label: row.label, photo: row.photo }],
    ),
  );
  const isPictured = $derived(credits.length > 0);
  const isOneParty = $derived.by(() => {
    const parties = rows.flatMap((row) => {
      const option = row.current ?? row.earlier;
      return option?.person == null ? [] : [option.party ?? null];
    });
    return (
      parties.length > 1 &&
      parties[0] != null &&
      parties.every((party) => party === parties[0])
    );
  });
  const tail = $derived.by(() => {
    let total = 0;
    let current = 0;
    let earlier = 0;
    for (const row of rows) {
      if (folded.has(row.key)) {
        total += row.current?.votes ?? 0;
        current += row.current?.share ?? 0;
        earlier += row.earlier?.share ?? 0;
      }
    }
    return {
      votes: total,
      before: span?.forward === true ? current : earlier,
      after: span?.forward === true ? earlier : current,
      current,
    };
  });
</script>

{#snippet trend(
  before: { share: number } | null,
  after: { share: number } | null,
)}
  <span
    class="relative block bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px)] bg-repeat-x block-4"
    style:background-size="calc(100% / {scale / step}) 100%"
  >
    {#if after != null}
      <span
        class="absolute inset-y-0.75 inset-s-0 animate-sweep rounded-e-xs bg-(--party) transition-[inline-size] duration-500 motion-reduce:animate-none motion-reduce:transition-none"
        style:inline-size="{(after.share / scale) * 100}%"
      ></span>
    {/if}
    {#if before != null && (after?.share ?? 0) < before.share}
      <span
        class="absolute inset-y-0.75 rounded-e-xs bg-(--party)/25"
        style:inset-inline-start="{((after?.share ?? 0) / scale) * 100}%"
        style:inline-size="{((before.share - (after?.share ?? 0)) / scale) *
          100}%"
      ></span>
    {/if}
    {#if before != null}
      <span
        class="absolute inset-y-0 -translate-x-1/2 bg-foreground ring-1 ring-card inline-0.5"
        style:inset-inline-start="{(before.share / scale) * 100}%"
      ></span>
    {/if}
  </span>
{/snippet}

{#snippet year(label: string, bar: boolean)}
  <span
    class={cn("flex items-center gap-1 px-0.5", {
      "highlighter font-semibold text-foreground": label === here,
    })}
  >
    <span
      class={cn("bg-current", {
        "rounded-xs block-2 inline-3": bar,
        "block-3 inline-0.5": !bar,
      })}
    ></span>
    {label}
  </span>
{/snippet}

{#snippet cite(
  text: string,
  measure: "votes" | "share" | "seats",
  row: (typeof rows)[number],
)}
  <button
    type="button"
    class="cursor-help underline decoration-transparent decoration-dotted underline-offset-4 hover:decoration-current focus-visible:decoration-current"
    onclick={() => {
      provenance.value = {
        label: `${row.label}, ${{ votes, share: m.col_share(), seats: seats ?? "" }[measure].toLocaleLowerCase()}`,
        figure: text,
        measure,
        path: trace,
        key: row.key,
        area: null,
      };
      provenance.isOpen = true;
    }}>{text}</button
  >
{/snippet}

{#if span != null && !paired}
  <p class="px-2 pbs-1 text-[0.8125rem] text-muted-foreground">
    {m.compare_unpaired()}
  </p>
{/if}
<Download
  class="-mbe-2 justify-end"
  name="results"
  columns={[
    isQuestion ? m.col_answer() : m.col_option(),
    votes,
    `${m.col_share()} (%)`,
    ...(seats == null ? [] : [seats]),
    m.col_outcome(),
    ...(span == null ? [] : [`${there ?? ""} (%)`]),
  ]}
  rows={() =>
    rows.flatMap((row) => [
      [
        row.label,
        row.current?.votes ?? null,
        row.current == null ? null : Math.round(row.current.share * 1e6) / 1e4,
        ...(seats == null ? [] : [row.current?.seats ?? null]),
        row.current?.outcome == null
          ? null
          : (OUTCOMES[row.current.outcome]?.() ?? row.current.outcome),
        ...(span == null
          ? []
          : [
              row.earlier == null
                ? null
                : Math.round(row.earlier.share * 1e6) / 1e4,
            ]),
      ],
      ...(row.current?.lines ?? []).map((line) => [
        `${row.label}, ${m.fusion_line({ name: line.label })}`,
        line.votes,
        row.current == null || row.current.votes === 0
          ? null
          : Math.round(
              (line.votes / row.current.votes) * row.current.share * 1e6,
            ) / 1e4,
        ...(seats == null ? [] : [null]),
        null,
        ...(span == null ? [] : [null]),
      ]),
    ])}
/>
<div class="overflow-x-auto">
  <table class="border-collapse text-sm/tight inline-full">
    <thead>
      <tr class="border-be text-[0.8125rem] text-muted-foreground">
        <th class="px-2 py-1.5 text-start font-medium" scope="col"
          >{isQuestion ? m.col_answer() : m.col_option()}</th
        >
        <th
          class={cn("px-2 py-1.5 text-end font-medium", {
            "max-sm:hidden": span != null,
          })}
          scope="col">{votes}</th
        >
        {#if span == null}
          <th class="px-2 py-1.5 text-end font-medium" scope="col"
            >{m.col_share()}</th
          >
        {:else}
          {#each [span.from, span.to] as label (label)}
            <th
              class="px-2 py-1.5 text-end font-medium whitespace-nowrap"
              scope="col"
            >
              <span
                class={cn("px-0.5", {
                  "highlighter font-semibold text-foreground": label === here,
                })}
                ><span class="max-sm:sr-only"
                  >{m.col_share_in({ year: label })}</span
                ><span class="sm:hidden" aria-hidden="true">{label}</span></span
              >
            </th>
          {/each}
        {/if}
        {#if seats != null}
          <th
            class={cn("px-2 py-1.5 text-end font-medium", {
              "max-sm:hidden": span != null,
            })}
            scope="col"
          >
            {seats}
          </th>
        {/if}
        {#if span != null && paired}
          <th
            class="px-2 pbs-1.5 pbe-1 text-start align-bottom font-medium inline-[26%] max-md:hidden"
            scope="col"
          >
            <span class="sr-only"
              >{m.trend_label({ from: span.from, to: span.to })}</span
            >
            <span class="flex items-center gap-2.5" aria-hidden="true">
              {@render year(span.from, false)}
              {@render year(span.to, true)}
            </span>
            <span
              class="relative mbs-1 block font-figures text-[0.6875rem] font-normal tabular-nums block-3.5"
              aria-hidden="true"
            >
              {#each ticks as tick, index (index)}
                <span
                  class={cn("absolute inset-bs-0", {
                    "-translate-x-1/2": index > 0 && tick < scale - 1e-9,
                    "-translate-x-full": index > 0 && tick >= scale - 1e-9,
                  })}
                  style:inset-inline-start="{(tick / scale) * 100}%"
                  >{whole.format(tick)}</span
                >
              {/each}
            </span>
          </th>
          <th class="px-2 py-1.5 text-end align-bottom font-medium" scope="col"
            ><Term
              label={m.col_change()}
              definition={m.change_title({ from: span.from, to: span.to })}
            /></th
          >
        {/if}
      </tr>
    </thead>
    <tbody>
      {#each shown as line (line.row.key)}
        {const row = $derived(line.row)}
        {const page = $derived(row.current)}
        {const other = $derived(row.earlier)}
        <tr
          class={cn("border-border/60", {
            "border-be": span != null || (page?.lines.length ?? 0) === 0,
            "text-muted-foreground": page == null,
            "animate-in fade-in motion-reduce:animate-none": folded.has(
              row.key,
            ),
          })}
          style:--party={swatch(row.color, row.key)}
        >
          <th class="px-2 py-1.5 text-start font-normal" scope="row">
            <span
              class={cn("flex items-center gap-2.5", {
                "min-block-9": isPictured,
              })}
            >
              {#if row.photo != null}
                <img
                  class="shrink-0 rounded-full bg-muted object-cover object-top ring-2 ring-(--party) ring-offset-1 ring-offset-card block-7 inline-7"
                  src={row.photo.src}
                  alt=""
                  loading="lazy"
                  width="28"
                  height="28"
                />
              {:else if isPictured && (page ?? other)?.person == null}
                <span
                  class="grid shrink-0 place-items-center block-7 inline-7"
                  aria-hidden="true"
                >
                  {#if (page ?? other)?.party != null}
                    <span
                      class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"
                    ></span>
                  {/if}
                </span>
              {:else if isPictured}
                <span
                  class="grid shrink-0 place-items-center rounded-full bg-secondary text-[0.625rem] font-semibold text-muted-foreground ring-2 ring-(--party) ring-offset-1 ring-offset-card select-none block-7 inline-7"
                  aria-hidden="true"
                  >{row.label
                    .split(/\s+/u)
                    .filter(
                      (word) => !/^(?:jr|sr|ii|iii|iv)\.?,?$/iu.test(word),
                    )
                    .filter((word, index, words) =>
                      [0, words.length - 1].includes(index),
                    )
                    .map((word) => word.slice(0, 1))
                    .join("")}</span
                >
              {:else}
                <span
                  class="shrink-0 rounded-xs bg-(--party) chip block-2.5 inline-2.5"
                ></span>
              {/if}
              <span class="flex-1 min-inline-0">
                <Name
                  class="font-semibold text-foreground"
                  label={row.label}
                  color={swatch(row.color, row.key)}
                  hasSwatch={false}
                  person={(page ?? other)?.person}
                  party={(page ?? other)?.party}
                />
                {#if page?.isIncumbent === true && page.person != null}
                  <span class="ms-1 text-xs text-muted-foreground"
                    >{m.results_incumbent()}</span
                  >
                {/if}
                {#if page?.outcome != null && seats == null}
                  {#if page.note == null}
                    <span
                      class={cn("ms-1 px-1 text-xs font-semibold", {
                        "highlighter text-foreground": !LOST.has(page.outcome),
                        "text-muted-foreground": LOST.has(page.outcome),
                      })}>{OUTCOMES[page.outcome]?.() ?? page.outcome}</span
                    >
                  {:else}
                    <Term
                      class={cn("ms-1 px-1 text-xs font-semibold", {
                        "highlighter text-foreground": !LOST.has(page.outcome),
                        "text-muted-foreground": LOST.has(page.outcome),
                      })}
                      label={OUTCOMES[page.outcome]?.() ?? page.outcome}
                      definition={explain(page.note)}
                    />
                  {/if}
                {/if}
                {#if row.short != null && !row.label.includes(row.short) && !isOneParty && (span != null || (page?.lines.length ?? 0) === 0)}
                  <span class="block text-[0.8125rem] text-pretty">
                    <Name
                      class="text-muted-foreground"
                      label={row.short}
                      color={swatch(row.color, row.key)}
                      hasSwatch={false}
                      party={(page ?? other)?.party}
                    />
                  </span>
                {:else if row.short == null && page?.person != null && page.isIndependent === true && !isOneParty}
                  <span
                    class="block text-[0.8125rem] text-pretty text-muted-foreground"
                    >{m.seats_independents()}</span
                  >
                {/if}
                {#if span != null && there != null}
                  {#if page == null}
                    <span class="block text-[0.8125rem] text-muted-foreground"
                      >{m.compare_absent({ year: here ?? "" })}</span
                    >
                  {:else if other == null}
                    <span class="block text-[0.8125rem] text-muted-foreground"
                      >{m.compare_absent({ year: there })}</span
                    >
                  {:else if other.label !== page.label}
                    {const parts = $derived(
                      m
                        .compare_in({ year: there, name: "\u0001" })
                        .split("\u0001"),
                    )}
                    <span class="block text-[0.8125rem] text-muted-foreground"
                      >{parts[0]}<Name
                        label={other.label}
                        person={other.person}
                        party={other.party}
                      />{parts[1]}</span
                    >
                  {/if}
                {/if}
                {#if row.backed != null}
                  <span class="block text-[0.8125rem] text-muted-foreground"
                    >{m.backed({ name: row.backed })}</span
                  >
                {/if}
                {#if row.origin != null}
                  <span class="block text-[0.8125rem] text-muted-foreground"
                    >{m.split_from({ name: row.origin })}</span
                  >
                {/if}
              </span>
            </span>
          </th>
          <td
            class={cn(
              "px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums",
              { "max-sm:hidden": span != null },
            )}
          >
            {#if page != null}
              {@render cite(count(page.votes), "votes", row)}
            {/if}
          </td>
          {#if span == null}
            <td
              class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums"
            >
              {#if page != null}
                <span class="relative inline-block min-inline-12">
                  {@render cite(percent(page.share, digits), "share", row)}
                  <span
                    class="absolute inset-x-0 inset-bs-[calc(100%+0.25rem)] flex justify-end overflow-hidden rounded-full bg-secondary block-1"
                    aria-hidden="true"
                  >
                    <span
                      class="rounded-full bg-(--party)"
                      style:inline-size="{(page.share / topShare) * 100}%"
                    ></span>
                  </span>
                </span>
              {/if}
            </td>
          {:else}
            {#each [line.before, line.after] as cell, index (index)}
              <td
                class={cn(
                  "px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums",
                  {
                    "font-semibold text-foreground":
                      cell != null && cell === page,
                    "text-muted-foreground": cell == null || cell !== page,
                  },
                )}
              >
                {cell == null ? "–" : percent(cell.share, digits)}
              </td>
            {/each}
          {/if}
          {#if seats != null}
            <td
              class={cn(
                "px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums",
                { "max-sm:hidden": span != null },
              )}
            >
              <span class="font-semibold"
                >{#if page?.seats != null}{@render cite(
                    count(page.seats),
                    "seats",
                    row,
                  )}{/if}</span
              >
              {#if line.gained != null && line.gained !== 0}
                <span class="ms-1 text-[0.75rem] text-muted-foreground"
                  >{new Intl.NumberFormat(getLocale(), {
                    signDisplay: "exceptZero",
                  }).format(line.gained)}</span
                >
              {/if}
            </td>
          {/if}
          {#if span != null && paired}
            <td class="px-2 py-1 max-md:hidden">
              {#if line.before != null || line.after != null}
                <Tooltip.Root disableCloseOnTriggerClick>
                  <Tooltip.Trigger
                    class="block cursor-default inline-full"
                    aria-label={m.trend_label({ from: span.from, to: span.to })}
                  >
                    {@render trend(line.before, line.after)}
                  </Tooltip.Trigger>
                  <Tooltip.Content
                    collisionPadding={8}
                    class="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-1 py-1.5 max-inline-80"
                    style="--party: {swatch(row.color, row.key)}"
                  >
                    {#each [{ year: span.from, cell: line.before, bar: false }, { year: span.to, cell: line.after, bar: true }] as side, index (index)}
                      <span
                        class={cn("self-center bg-(--party)", {
                          "rounded-xs block-2 inline-3": side.bar,
                          "bg-background block-3 inline-0.5": !side.bar,
                        })}
                      ></span>
                      <span class="font-figures tabular-nums opacity-70"
                        >{side.year}</span
                      >
                      {#if side.cell == null}
                        <span class="col-span-2 opacity-70"
                          >{m.compare_absent({ year: side.year })}</span
                        >
                      {:else}
                        <span class="truncate">{side.cell.label}</span>
                        <span class="text-end font-figures tabular-nums"
                          >{percent(side.cell.share)}</span
                        >
                      {/if}
                    {/each}
                    {#if line.change != null}
                      <span
                        class="col-span-4 border-bs border-background/20 pbs-1 text-end font-figures font-semibold tabular-nums"
                        >{m.trend_change({ points: points(line.change) })}</span
                      >
                    {/if}
                  </Tooltip.Content>
                </Tooltip.Root>
              {/if}
            </td>
            <td
              class="px-2 py-1.5 text-end font-figures text-[0.8125rem] font-semibold whitespace-nowrap tabular-nums"
            >
              {line.change == null ? "" : points(line.change)}
            </td>
          {/if}
        </tr>
        {#if span == null && page != null}
          {#each page.lines as fused, index (fused.label)}
            <tr
              class={cn("text-[0.8125rem] text-muted-foreground", {
                "border-be border-border/60": index === page.lines.length - 1,
              })}
            >
              <th class="ps-6.5 pe-2 pbe-1 text-start font-normal" scope="row">
                {m.fusion_line({ name: fused.label })}
              </th>
              <td
                class="px-2 pbe-1 text-end font-figures whitespace-nowrap tabular-nums"
              >
                {count(fused.votes)}
              </td>
              <td
                class="px-2 pbe-1 text-end font-figures whitespace-nowrap tabular-nums"
              >
                {page.votes === 0
                  ? ""
                  : percent((fused.votes / page.votes) * page.share)}
              </td>
              {#if seats != null}
                <td></td>
              {/if}
            </tr>
          {/each}
        {/if}
      {/each}
      {#if folded.size > 0}
        <tr>
          <th class="px-2 py-1.5 text-start font-normal" scope="row">
            <button
              type="button"
              class="flex items-center rounded-sm border border-input bg-card px-2 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 hover:bg-secondary"
              aria-expanded={expanded}
              onclick={() => (expanded = !expanded)}
              >{expanded
                ? m.results_fewer()
                : m.results_more({ count: folded.size })}</button
            >
          </th>
          {#if !expanded}
            <td
              class={cn(
                "px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums",
                { "max-sm:hidden": span != null },
              )}>{count(tail.votes)}</td
            >
            {#if span == null}
              <td
                class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
                >{percent(tail.current)}</td
              >
            {:else}
              {#each [tail.before, tail.after] as share, index (index)}
                <td
                  class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums"
                  >{percent(share)}</td
                >
              {/each}
            {/if}
          {/if}
        </tr>
      {/if}
    </tbody>
  </table>
</div>
{#if credits.length > 0}
  <details class="group px-2 pbs-2 text-xs/relaxed text-muted-foreground">
    <summary
      class="flex cursor-pointer list-none items-center gap-1 rounded-xs inline-fit hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
    >
      <ChevronRightIcon
        class="transition-transform block-3.5 inline-3.5 group-open:rotate-90"
      />
      {m.results_photos()}
    </summary>
    <ul class="flex flex-wrap gap-x-3 ps-4.5 pbs-1">
      {#each credits as row (row.key)}
        <li>
          {row.label}:
          <a
            class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
            href={row.photo.source}
            rel="external noopener">{credit(row.photo)}</a
          >
        </li>
      {/each}
    </ul>
  </details>
{/if}
