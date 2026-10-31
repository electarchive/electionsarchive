<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { Input } from "#lib/components/ui/input/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  type Item = {
    anchor: string;
    label: string;
    swatch: string | null;
    lead: Array<{ key: string; label: string; color: string; value: string }>;
    detail: string | null;
    parts: Array<{ key: string; color: string; value: number }>;
    total: number;
  };

  const {
    sections,
    current,
    href,
    select,
    contests,
  }: {
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

  const uid = $props.id();
  let query = $state("");
  let isOpen = $state(false);

  const fold = (text: string) =>
    text.normalize("NFD").replaceAll(/\p{M}/gu, "").toLowerCase();
  const tokens = $derived(fold(query).split(/\s+/u).filter(Boolean));
  const matches = (...texts: Array<string | null>) => {
    const haystack = fold(texts.filter((text) => text != null).join(" "));
    return tokens.every((token) => haystack.includes(token));
  };
  const words = (item: Item) => [
    item.label,
    item.detail,
    ...item.lead.map((part) => part.label),
  ];

  const flat = $derived(
    sections.flatMap((section) =>
      section.groups.flatMap((group) => [
        ...(group.head == null ? [] : [group.head]),
        ...group.items,
      ]),
    ),
  );
  const at = $derived(flat.findIndex((item) => item.anchor === current));
  const filtered = $derived(
    tokens.length === 0
      ? sections
      : sections
          .map((section) => ({
            ...section,
            groups: section.groups.flatMap((group) => {
              if (matches(group.title)) {
                return [group];
              }
              const items = group.items.filter((item) =>
                matches(group.title, ...words(item)),
              );
              const head =
                group.head != null && matches(...words(group.head))
                  ? group.head
                  : null;
              return items.length === 0 && head == null
                ? []
                : [{ ...group, items }];
            }),
          }))
          .filter((section) => section.groups.length > 0),
  );
  const found = $derived(
    tokens.length === 0
      ? []
      : contests.filter((contest) => matches(contest.name)),
  );

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
    isOpen = false;
    select(anchor);
  };
  const submit = (event: KeyboardEvent) => {
    if (event.key !== "Enter") {
      return;
    }
    const first = filtered[0]?.groups[0];
    const target = first?.head ?? first?.items[0];
    if (target != null) {
      isOpen = false;
      select(target.anchor);
    } else if (found[0] != null) {
      void goto(resolve("/[...path]", { path: found[0].path }));
    }
  };
</script>

{#snippet entry(item: Item, isNested: boolean, isHead: boolean)}
  {const isCurrent = $derived(item.anchor === current)}
  {const shown = $derived(item.parts.filter((part) => part.value > 0))}
  {const spare = $derived.by(() => {
    let left = item.total;
    for (const part of shown) {
      left -= part.value;
    }
    return Math.max(0, left);
  })}
  <a
    href={href(item.anchor)}
    aria-current={isCurrent ? "page" : undefined}
    class={cn(
      "grid gap-1 border-s-2 border-transparent py-1.5 pe-3.5 keycap hover:bg-secondary focus-visible:relative focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-foreground",
      { "ps-3": !isNested, "ps-6": isNested },
      { "border-foreground bg-secondary": isCurrent },
    )}
    onclick={(event) => choose(event, item.anchor)}
  >
    <span
      class={cn("flex items-center gap-1.5 text-pretty", {
        "text-sm/snug font-semibold": !isHead,
        "text-[0.8125rem]/snug font-bold": isHead,
      })}
    >
      {#if item.swatch != null}
        <span
          class="shrink-0 rounded-xs block-2.5 inline-2.5"
          style:background={item.swatch}
        ></span>
      {/if}
      <span class="line-clamp-2">{item.label}</span>
    </span>
    {#if item.detail != null}
      <span
        class="line-clamp-2 text-[0.8125rem]/snug text-pretty text-muted-foreground"
        >{item.detail}</span
      >
    {/if}
    {#if item.lead.length > 0}
      <span
        class="flex items-baseline gap-x-2.5 text-[0.8125rem] text-muted-foreground min-inline-0"
      >
        {#each item.lead as part (part.key)}
          <span class="flex items-baseline gap-1 min-inline-0">
            <span
              class="shrink-0 self-center rounded-[1px] block-2 inline-2"
              style:background={part.color}
            ></span>
            <span class="truncate">{part.label}</span>
            <span class="shrink-0 font-figures text-foreground tabular-nums"
              >{part.value}</span
            >
          </span>
        {/each}
      </span>
    {/if}
    {#if shown.length > 0}
      <span
        class="flex overflow-hidden rounded-[1px] bg-border block-0.75"
        aria-hidden="true"
      >
        {#each shown as part (part.key)}
          <span
            class="basis-0"
            style:flex-grow={part.value}
            style:background={part.color}
          ></span>
        {/each}
        {#if spare > 0}
          <span class="basis-0" style:flex-grow={spare}></span>
        {/if}
      </span>
    {/if}
  </a>
{/snippet}

<div
  class="z-20 bg-card max-lg:sticky max-lg:inset-bs-0 max-lg:border-be max-lg:border-border"
>
  <div class="lg:sticky lg:inset-bs-0 lg:overflow-y-auto lg:max-block-svh">
    <button
      type="button"
      class="flex items-center gap-3 px-3.5 text-start block-14 inline-full lg:hidden"
      aria-expanded={isOpen}
      aria-controls="{uid}-panel"
      onclick={() => (isOpen = !isOpen)}
    >
      <span class="grid min-inline-0">
        <span class="text-xs text-muted-foreground"
          >{at === -1
            ? m.election_nav_choose()
            : m.election_nav_position({
                index: count(at + 1),
                count: count(flat.length),
              })}</span
        >
        <span class="truncate text-[0.9375rem] font-semibold"
          >{flat[at]?.label ?? m.election_nav_overview()}</span
        >
      </span>
      <ChevronDownIcon
        class={cn("ms-auto shrink-0 transition-transform block-5 inline-5", {
          "rotate-180": isOpen,
        })}
        aria-hidden="true"
      />
    </button>
    <nav
      id="{uid}-panel"
      class={cn(
        "grid content-start gap-3 pbs-3 pbe-4 max-lg:overflow-y-auto max-lg:border-bs max-lg:border-border max-lg:max-block-[calc(100svh-3.5rem)]",
        { "max-lg:hidden": !isOpen },
      )}
      aria-label={m.election_nav_label()}
    >
      <div class="px-3.5">
        <Input
          class="block-8 inline-full"
          type="search"
          placeholder={m.election_nav_find()}
          aria-label={m.election_nav_find()}
          bind:value={query}
          onkeydown={submit}
        />
      </div>
      {#if tokens.length === 0}
        <a
          href={href(null)}
          aria-current={current == null ? "page" : undefined}
          class={cn(
            "border-s-2 border-transparent py-1.5 ps-3 pe-3.5 text-[0.875rem] font-semibold keycap hover:bg-secondary focus-visible:relative focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-foreground",
            { "border-foreground bg-secondary": current == null },
          )}
          onclick={(event) => choose(event, null)}
          >{m.election_nav_overview()}</a
        >
      {/if}
      {#each filtered as section (section.key)}
        <section class="grid gap-1" aria-labelledby="{uid}-{section.key}">
          {#if section.title != null}
            <h2
              id="{uid}-{section.key}"
              class="px-3.5 text-xs font-semibold text-muted-foreground"
            >
              {section.title}
            </h2>
          {/if}
          <ul class="grid gap-px">
            {#each section.groups as group (group.key)}
              <li class="grid gap-px">
                {#if group.title == null}
                  {#each group.items as item (item.anchor)}
                    {@render entry(item, false, false)}
                  {/each}
                {:else}
                  {#if group.head == null}
                    <p class="px-3.5 pbs-1 text-[0.8125rem] font-bold">
                      {group.title}
                    </p>
                  {:else}
                    {@render entry(group.head, false, true)}
                  {/if}
                  <ul class="grid gap-px">
                    {#each group.items as item (item.anchor)}
                      <li>{@render entry(item, true, false)}</li>
                    {/each}
                  </ul>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/each}
      {#if found.length > 0}
        <section class="grid gap-1" aria-labelledby="{uid}-contests">
          <h2
            id="{uid}-contests"
            class="px-3.5 text-xs font-semibold text-muted-foreground"
          >
            {m.election_nav_contests()}
          </h2>
          <ul class="grid gap-px text-[0.8125rem]/snug">
            {#each found.slice(0, 8) as contest (contest.path)}
              <li>
                <a
                  class="block px-3.5 py-1.5 text-pretty keycap hover:bg-secondary"
                  href={resolve("/[...path]", { path: contest.path })}
                  >{contest.name.replace(/^\d{4} /u, "")}</a
                >
              </li>
            {/each}
          </ul>
          {#if found.length > 8}
            <p class="px-3.5 text-xs text-muted-foreground">
              {m.election_nav_more({ count: count(found.length - 8) })}
            </p>
          {/if}
        </section>
      {/if}
      {#if tokens.length > 0 && filtered.length === 0 && found.length === 0}
        <p class="px-3.5 text-[0.8125rem] text-pretty text-muted-foreground">
          {m.election_nav_empty({ query: query.trim() })}
        </p>
      {/if}
    </nav>
  </div>
</div>
