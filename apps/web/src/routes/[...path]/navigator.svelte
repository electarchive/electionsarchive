<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
  import { SvelteSet } from "svelte/reactivity";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Item = {
    anchor: string;
    label: string;
    leader: { label: string; color: string } | null;
    figure: string | null;
    detail: string | null;
    tint: string | null;
    parts: Array<{ key: string; color: string; value: number }>;
    total: number;
  };

  const {
    class: className,
    sections,
    current,
    href,
    select,
    contests,
  }: {
    class?: string;
    sections: Array<{
      key: string;
      title: string | null;
      groups: Array<{
        key: string;
        title: string | null;
        head: Item | null;
        items: Array<Item>;
      }>;
    }>;
    current: string | null;
    href: (anchor: string | null) => string;
    select: (anchor: string | null) => void;
    contests: Array<{ name: string; path: string }>;
  } = $props();

  const LIMIT = 6;
  const OVERVIEW = "#overview";
  const ELSEWHERE = "path:";

  const opened = new SvelteSet<string>();

  const board = $derived(
    sections.map((section) => {
      const rows = section.groups.flatMap((group) =>
        group.title == null
          ? group.items.map((item) => ({
              group: group.key,
              item,
              caption: null,
              isNested: false,
            }))
          : [
              group.head == null
                ? {
                    group: group.key,
                    item: null,
                    caption: group.title,
                    isNested: false,
                  }
                : {
                    group: group.key,
                    item: { ...group.head, label: group.title },
                    caption: null,
                    isNested: false,
                  },
              ...group.items.map((item) => ({
                group: group.key,
                item,
                caption: null,
                isNested: group.head != null,
              })),
            ],
      );
      const races = rows.flatMap((row) => row.item ?? []);
      const isOpen = opened.has(section.key) || races.length <= LIMIT + 1;
      const kept = new Set(
        races
          .filter(
            (item, index) => isOpen || index < LIMIT || item.anchor === current,
          )
          .map((item) => item.anchor),
      );
      const groups = new Set(
        rows.flatMap((row) =>
          row.item != null && kept.has(row.item.anchor) ? [row.group] : [],
        ),
      );
      return {
        key: section.key,
        title: section.title,
        rows: rows.filter((row) =>
          row.item == null ? groups.has(row.group) : kept.has(row.item.anchor),
        ),
        hidden: races.filter((item) => !kept.has(item.anchor)),
      };
    }),
  );
  const total = $derived(
    sections
      .flatMap((section) => section.groups)
      .flatMap((group) => [
        ...(group.head == null ? [] : [group.head]),
        ...group.items,
      ]).length,
  );

  const items = $derived([
    {
      value: OVERVIEW,
      label: m.election_nav_overview(),
      group: "",
      item: null,
    },
    ...sections.flatMap((section) =>
      section.groups.flatMap((group) =>
        [
          ...(group.head == null
            ? []
            : [{ ...group.head, label: group.title ?? group.head.label }]),
          ...group.items,
        ].map((item) => ({
          value: item.anchor,
          label: item.label,
          group: group.title ?? section.title ?? "",
          keywords: [
            ...(group.title == null ? [] : [group.title]),
            ...(item.detail == null ? [] : [item.detail]),
            ...(item.leader == null ? [] : [item.leader.label]),
          ],
          item,
        })),
      ),
    ),
    ...contests.map((contest) => ({
      value: `${ELSEWHERE}${contest.path}`,
      label: contest.name.replace(/^\d{4} /u, ""),
      group: m.election_nav_contests(),
      item: null,
    })),
  ]);

  const choose = (event: MouseEvent, anchor: string | null) => {
    if (
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    event.preventDefault();
    select(anchor);
  };
</script>

{#snippet bar(item: Item)}
  {const shown = $derived(item.parts.filter((part) => part.value > 0))}
  {const spare = $derived.by(() => {
    let left = item.total;
    for (const part of shown) {
      left -= part.value;
    }
    return Math.max(0, left);
  })}
  <span
    class="relative col-start-3 row-span-2 row-start-1 ms-3 self-stretch @lg:col-start-5 @lg:row-span-1 @lg:ms-5"
    aria-hidden="true"
  >
    {#if shown.length > 0}
      <span
        class="absolute inset-x-0 inset-bs-1/2 flex -translate-y-1/2 overflow-hidden rounded-[1px] bg-muted block-2.5"
      >
        {#each shown as part (part.key)}
          <span
            class="basis-0 not-last:border-e not-last:border-card"
            style:flex-grow={part.value}
            style:background={part.color}
          ></span>
        {/each}
        {#if spare > 0}
          <span class="basis-0" style:flex-grow={spare}></span>
        {/if}
      </span>
    {/if}
    <span
      class="absolute inset-s-1/2 inset-bs-0 -inset-be-px bg-foreground inline-px"
    ></span>
  </span>
{/snippet}

<nav
  class={cn(
    "@container grid content-start [--gutter:--spacing(3.5)] min-inline-0 lg:-mx-2.5 lg:[--gutter:--spacing(2.5)] max-lg:-mx-3.5",
    className,
  )}
  aria-label={m.election_nav_label()}
>
  <div class="flex items-center justify-between gap-3 px-(--gutter) pbe-2">
    <a
      class={cn(
        "text-[0.875rem] font-semibold underline decoration-2 underline-offset-[0.4em] keycap",
        {
          "decoration-foreground": current == null,
          "text-muted-foreground decoration-transparent hover:text-foreground hover:decoration-border":
            current != null,
        },
      )}
      href={href(null)}
      aria-current={current == null ? "page" : undefined}
      onclick={(event) => choose(event, null)}>{m.election_nav_overview()}</a
    >
    <Combobox
      wide
      {items}
      value={current ?? OVERVIEW}
      label={m.election_nav_all_label({ count: count(total) })}
      placeholder=""
      search={m.election_nav_find()}
      empty={m.election_nav_none()}
      onValueChange={(value: string | null) => {
        if (value == null) {
          return;
        }
        if (value.startsWith(ELSEWHERE)) {
          void goto(
            resolve("/[...path]", { path: value.slice(ELSEWHERE.length) }),
          );
        } else {
          select(value === OVERVIEW ? null : value);
        }
      }}
    >
      {#snippet trigger(props: Record<string, unknown>)}
        <button
          {...props}
          type="button"
          class="flex shrink-0 items-center gap-1.5 rounded-sm border border-input bg-card ps-2.5 pe-2 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7.5 hover:bg-secondary"
          aria-label={m.election_nav_all_label({ count: count(total) })}
          >{m.series_all({ count: count(total) })}<ChevronsUpDownIcon
            class="text-muted-foreground block-3.5 inline-3.5"
            aria-hidden="true"
          /></button
        >
      {/snippet}
      {#snippet row(entry: (typeof items)[number])}
        <span class="flex-1 text-pretty min-inline-0">{entry.label}</span>
        {#if entry.item?.leader != null}
          <span
            class="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground"
          >
            <span
              class="shrink-0 rounded-[1px] block-2 inline-2"
              style:background={entry.item.leader.color}
            ></span>
            {#if entry.item.figure != null}
              <span class="font-figures text-foreground tabular-nums"
                >{entry.item.figure}</span
              >
            {/if}
          </span>
        {/if}
      {/snippet}
    </Combobox>
  </div>

  <div
    class={cn("grid content-start", {
      "gap-y-4 lg:grid-cols-2 lg:gap-x-12": board.length > 1,
    })}
  >
    {#each board as section (section.key)}
      <div class="grid content-start">
        {#if section.title != null}
          <h2
            class="px-(--gutter) pbe-1 text-[0.8125rem] font-semibold text-muted-foreground"
          >
            {section.title}
          </h2>
        {/if}
        <ol
          class="grid grid-cols-[var(--gutter)_minmax(0,1fr)_minmax(5.5rem,30%)_var(--gutter)] border-bs border-foreground @lg:grid-cols-[var(--gutter)_minmax(0,max-content)_minmax(0,max-content)_auto_minmax(6rem,1fr)_var(--gutter)]"
        >
          {#each section.rows as row (row.item?.anchor ?? `caption:${row.group}`)}
            <li
              class="col-span-full grid grid-cols-subgrid border-be border-border"
            >
              {#if row.item == null}
                <span
                  class="col-start-2 -col-end-2 pbs-2.5 pbe-1 text-[0.8125rem] font-semibold text-muted-foreground"
                  >{row.caption}</span
                >
              {:else}
                {const item = $derived(row.item)}
                {const isCurrent = $derived(item.anchor === current)}
                <a
                  class="relative col-span-full grid grid-cols-subgrid -outline-offset-2 keycap hover:bg-secondary aria-[current=page]:bg-accent"
                  href={href(item.anchor)}
                  aria-current={isCurrent ? "page" : undefined}
                  onclick={(event) => choose(event, item.anchor)}
                >
                  {#if isCurrent}
                    <span
                      class="absolute inset-y-0 inset-s-0 bg-foreground inline-0.75"
                      aria-hidden="true"
                    ></span>
                  {/if}
                  <span
                    class={cn(
                      "col-start-2 row-start-1 mbs-1.5 line-clamp-2 self-end text-[0.9375rem]/tight text-pretty @lg:my-1.5 @lg:self-center",
                      {
                        "font-semibold": !row.isNested || isCurrent,
                        "ps-3": row.isNested,
                      },
                    )}>{item.label}</span
                  >
                  <span
                    class={cn(
                      "col-start-2 row-start-2 flex items-baseline gap-3 pbs-0.5 pbe-1.5 text-sm min-inline-0 @lg:contents",
                      { "ps-3": row.isNested },
                    )}
                  >
                    {#if item.leader != null}
                      <span
                        class="flex items-baseline gap-1.5 min-inline-0 @lg:col-start-3 @lg:row-start-1 @lg:self-center @lg:ps-5"
                      >
                        <span
                          class="shrink-0 self-center rounded-[1px] block-2.5 inline-2.5"
                          style:background={item.leader.color}
                        ></span>
                        <span class="truncate">{item.leader.label}</span>
                      </span>
                    {:else if item.detail != null}
                      <span
                        class="truncate text-muted-foreground @lg:col-start-3 @lg:col-end-5 @lg:row-start-1 @lg:self-center @lg:ps-5"
                        >{item.detail}</span
                      >
                    {/if}
                    {#if item.figure != null}
                      <span
                        class="ms-auto shrink-0 font-figures font-semibold whitespace-nowrap tabular-nums @lg:col-start-4 @lg:row-start-1 @lg:ms-0 @lg:self-center @lg:ps-4 @lg:text-end"
                        >{item.figure}</span
                      >
                    {/if}
                  </span>
                  {@render bar(item)}
                </a>
              {/if}
            </li>
          {/each}
          {#if section.hidden.length > 0}
            <li class="col-span-full grid grid-cols-subgrid">
              <button
                type="button"
                class="col-span-full grid grid-cols-subgrid py-2.5 text-start -outline-offset-2 keycap hover:bg-secondary"
                aria-label={m.election_nav_more_label({
                  count: count(section.hidden.length),
                })}
                onclick={() => opened.add(section.key)}
              >
                <span
                  class="col-start-2 -col-end-2 flex items-center gap-3 text-[0.8125rem] font-semibold"
                >
                  <span class="shrink-0"
                    >{m.election_nav_more({
                      count: count(section.hidden.length),
                    })}</span
                  >
                  <span class="flex flex-wrap gap-0.5" aria-hidden="true">
                    {#each section.hidden as item (item.anchor)}
                      <span
                        class="rounded-[1px] bg-muted block-2.5 inline-2.5"
                        style:background={item.tint}
                      ></span>
                    {/each}
                  </span>
                </span>
              </button>
            </li>
          {/if}
        </ol>
      </div>
    {/each}
  </div>
</nav>
