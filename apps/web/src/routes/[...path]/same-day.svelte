<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
  import { Combobox } from "#lib/components/ui/combobox/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  const {
    place,
    races,
    day = null,
  }: {
    place: string;
    races: Array<{
      id: string;
      name: string;
      path: string;
      isCurrent: boolean;
    }>;
    day?: {
      place: string;
      part: { name: string; path: string | null } | null;
      groups: Array<{
        key: string;
        kind: string;
        label: string;
        count: number;
        path: string;
        anchor: string | null;
      }>;
    } | null;
  } = $props();

  const uid = $props.id();
  let isOpen = $state(false);

  const entries = $derived.by(() => {
    if (races.length < 2) {
      return [];
    }
    const home = place.split(" ");
    const isHome = (words: Array<string>, from: number) =>
      home.every((word, at) => words[from + at] === word);
    const split = races.map((race) => {
      const words = race.name.split(" ").filter((word) => word !== "election");
      if (
        words.length > home.length + 2 &&
        words.at(-home.length - 1) === "in" &&
        isHome(words, words.length - home.length)
      ) {
        words.splice(-home.length - 1);
      }
      const from = /^\d{4}$/.test(words[0] ?? "") ? 1 : 0;
      if (words.length > from + home.length + 1 && isHome(words, from)) {
        words.splice(from, home.length);
      }
      return words;
    });
    const shortest = Math.min(...split.map((words) => words.length));
    let lead = 0;
    while (
      lead < shortest - 1 &&
      split.every((words) => words[lead] === split[0]?.[lead])
    ) {
      lead += 1;
    }
    const lasts = new Map<string, number>();
    for (const words of split) {
      const last = words.at(-1) ?? "";
      lasts.set(last, (lasts.get(last) ?? 0) + 1);
    }
    let tail = "";
    for (const [word, seen] of lasts) {
      if (
        seen > 1 &&
        seen * 2 >= split.length &&
        seen > (lasts.get(tail) ?? 0)
      ) {
        tail = word;
      }
    }
    const shorten = (words: Array<string>) => {
      const kept = words.slice(lead);
      const text = (
        kept.length > 1 && kept.at(-1) === tail ? kept.slice(0, -1) : kept
      ).join(" ");
      return text.charAt(0).toLocaleUpperCase() + text.slice(1);
    };
    const heads = new Map<string, number>();
    const parts = races.map((race, index) => {
      const label = shorten(split[index] ?? []);
      const cut = label.indexOf(": ");
      const head = cut === -1 ? null : label.slice(0, cut);
      if (head != null) {
        heads.set(head, (heads.get(head) ?? 0) + 1);
      }
      return {
        race,
        label,
        head,
        rest: cut === -1 ? label : label.slice(cut + 2),
      };
    });
    const grouped: Array<{
      key: string;
      head: string | null;
      isCurrent: boolean;
      items: Array<{ label: string; path: string; isCurrent: boolean }>;
    }> = [];
    const byHead = new Map<string, (typeof grouped)[number]>();
    for (const part of parts) {
      const isShared = part.head != null && (heads.get(part.head) ?? 0) > 1;
      const item = {
        label: isShared ? part.rest : part.label,
        path: part.race.path,
        isCurrent: part.race.isCurrent,
      };
      const existing = isShared ? byHead.get(part.head ?? "") : undefined;
      if (existing == null) {
        const entry = {
          key: part.race.path,
          head: isShared ? part.head : null,
          isCurrent: item.isCurrent,
          items: [item],
        };
        grouped.push(entry);
        if (isShared) {
          byHead.set(part.head ?? "", entry);
        }
      } else {
        existing.items.push(item);
        existing.isCurrent ||= item.isCurrent;
      }
    }
    return grouped;
  });

  const tallies = $derived.by(() => {
    if (day == null) {
      return [];
    }
    const listed = new Set(
      entries.length === 0 ? [] : races.map((race) => race.path),
    );
    const isHere = entries.length > 0 && day.place === place;
    return day.groups.filter(
      (group) =>
        group.label !== "" &&
        !(
          isHere &&
          (group.kind === "governor" ||
            group.kind === "regional_office" ||
            group.kind === "region_office")
        ) &&
        !(group.anchor == null && listed.has(group.path)),
    );
  });

  const LIMIT = 10;
  const hidden = $derived(
    entries.filter((entry, index) => index >= LIMIT && !entry.isCurrent).length,
  );

  const href = (path: string, anchor: string | null = null) =>
    `${resolve("/[...path]", { path })}${anchor == null ? "" : `#${anchor}`}`;

  const items = $derived.by(() => {
    const listed = entries.length === 0 ? [] : races;
    const grouped =
      day == null || day.place === place
        ? m.same_day_grouped()
        : m.cycle_in_place({ place: day.place });
    return [
      ...listed.map((race) => ({
        value: race.path,
        href: href(race.path),
        label: race.name,
        group: m.same_day_title({ place }),
      })),
      ...(day?.part?.path == null
        ? []
        : [
            {
              value: "part",
              href: href(day.part.path),
              label: day.part.name,
              group: grouped,
            },
          ]),
      ...tallies.map((group) => ({
        value: group.key,
        href: href(group.path, group.anchor),
        label: group.label,
        detail: count(group.count),
        group: grouped,
      })),
    ];
  });
</script>

{#snippet link(
  label: string,
  path: string,
  isCurrent: boolean,
  isLast: boolean,
)}
  <span class="inline-flex items-baseline max-inline-full">
    {#if isCurrent}
      <span
        class="-mx-0.5 bg-marker/45 px-0.5 font-semibold dark:bg-marker dark:text-[#15171a]"
        aria-current="page">{label}</span
      >
    {:else}
      <Preview
        class="underline decoration-border underline-offset-4 hover:decoration-foreground"
        {path}>{label}</Preview
      >
    {/if}
    {#if !isLast}
      <span class="me-[0.3em] text-muted-foreground">,</span>
    {/if}
  </span>
{/snippet}

{#snippet tally(group: (typeof tallies)[number], isStrip: boolean)}
  <a
    class={cn("group inline-flex items-baseline gap-1.5", {
      "items-center whitespace-nowrap block-8": isStrip,
    })}
    href={href(group.path, group.anchor)}
  >
    <span class="font-figures font-semibold tabular-nums"
      >{count(group.count)}</span
    >
    <span
      class="underline decoration-border underline-offset-4 group-hover:decoration-foreground"
      >{group.label}</span
    >
  </a>
{/snippet}

{#if entries.length > 0 || tallies.length > 0}
  <nav
    class="grid gap-1.5 text-[0.8125rem]/snug min-inline-0 md:col-span-2 max-sm:gap-0.5"
    aria-labelledby="{uid}-title"
  >
    <h2 id="{uid}-title" class="font-bold">
      {m.same_day_title({ place })}
    </h2>
    <div class="flex items-center gap-1.5 min-inline-0 sm:hidden">
      <ul
        class="scroll-fade-x flex flex-1 items-center gap-x-4 min-inline-0"
        {@attach (strip: HTMLElement) => {
          void document.fonts.ready.then(() => {
            const current = strip.querySelector('[aria-current="page"]');
            if (current == null) {
              return;
            }
            const box = current.getBoundingClientRect();
            const frame = strip.getBoundingClientRect();
            if (box.left < frame.left || box.right > frame.right) {
              strip.scrollBy({ left: box.left - frame.left - 32 });
            }
          });
        }}
      >
        {#each entries as entry (entry.key)}
          <li class="flex items-center whitespace-nowrap block-8">
            {#if entry.head != null}
              <span class="me-1 text-muted-foreground">{entry.head}:</span>
            {/if}
            {#each entry.items as item, at (item.path)}
              {@render link(
                item.label,
                item.path,
                item.isCurrent,
                at === entry.items.length - 1,
              )}
            {/each}
          </li>
        {/each}
        {#if entries.length > 0 && (tallies.length > 0 || day?.part?.path != null)}
          <li
            class="shrink-0 border-s border-border block-4"
            aria-hidden="true"
          ></li>
        {/if}
        {#if day != null && day.place !== place}
          <li class="whitespace-nowrap text-muted-foreground">
            {m.cycle_in_place({ place: day.place })}
          </li>
        {/if}
        {#if day?.part?.path != null}
          <li class="flex items-center whitespace-nowrap block-8">
            <Preview
              class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              path={day.part.path}>{day.part.name}</Preview
            >
          </li>
        {/if}
        {#each tallies as group (group.key)}
          <li>{@render tally(group, true)}</li>
        {/each}
      </ul>
      {#if items.length > 3}
        <Combobox
          {items}
          value={entries.length === 0
            ? null
            : (races.find((race) => race.isCurrent)?.path ?? null)}
          label={m.same_day_all_label({ place })}
          placeholder=""
          search={m.same_day_search()}
          empty={m.same_day_empty()}
          onValueChange={(value) => {
            const target = items.find((item) => item.value === value);
            if (target != null) {
              void goto(target.href);
            }
          }}
        >
          {#snippet trigger(props: Record<string, unknown>)}
            <button
              {...props}
              type="button"
              class="flex shrink-0 items-center gap-1.5 rounded-sm border border-input bg-card ps-2.5 pe-2 font-semibold whitespace-nowrap keycap block-8 hover:bg-secondary"
              aria-label={m.same_day_all_label({ place })}
              >{m.same_day_all()}<ChevronsUpDownIcon
                class="text-muted-foreground block-3.5 inline-3.5"
              /></button
            >
          {/snippet}
        </Combobox>
      {/if}
    </div>
    {#if entries.length > 0}
      <ul class="flex flex-wrap items-baseline gap-x-4 gap-y-1 max-sm:hidden">
        {#each entries as entry, index (entry.key)}
          <li
            class={cn("max-inline-full", {
              hidden: !isOpen && index >= LIMIT && !entry.isCurrent,
            })}
          >
            {#if entry.head != null}
              <span class="text-muted-foreground">{entry.head}:</span>
            {/if}
            {#each entry.items as item, at (item.path)}
              {@render link(
                item.label,
                item.path,
                item.isCurrent,
                at === entry.items.length - 1,
              )}
            {/each}
          </li>
        {/each}
        {#if hidden > 0}
          <li>
            <button
              type="button"
              class="font-semibold text-muted-foreground underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-foreground"
              aria-expanded={isOpen}
              onclick={() => (isOpen = !isOpen)}
            >
              {isOpen
                ? m.same_day_fewer()
                : m.same_day_more({ count: count(hidden) })}
            </button>
          </li>
        {/if}
      </ul>
    {/if}
    {#if tallies.length > 0 || day?.part?.path != null}
      <ul
        class={cn(
          "flex flex-wrap items-baseline gap-x-4 gap-y-2 max-sm:hidden",
          { "border-bs border-border pbs-1.5": entries.length > 0 },
        )}
      >
        {#if day != null && day.place !== place}
          <li class="text-muted-foreground">
            {m.cycle_in_place({ place: day.place })}
          </li>
        {/if}
        {#if day?.part?.path != null}
          <li>
            <Preview
              class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              path={day.part.path}>{day.part.name}</Preview
            >
          </li>
        {/if}
        {#each tallies as group (group.key)}
          <li>{@render tally(group, false)}</li>
        {/each}
      </ul>
    {/if}
  </nav>
{/if}
