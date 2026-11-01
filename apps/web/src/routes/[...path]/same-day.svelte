<script lang="ts">
  import { resolve } from "$app/paths";
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
    const split = races.map((race) => race.name.split(" "));
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
      if (seen * 2 >= split.length && seen > (lasts.get(tail) ?? 0)) {
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
          (group.kind === "governor" || group.kind === "regional_office")
        ) &&
        !(group.anchor == null && listed.has(group.path)),
    );
  });

  const NARROW = 4;
  const WIDE = 10;
  const hidden = $derived({
    narrow: entries.filter(
      (entry, index) => index >= NARROW && !entry.isCurrent,
    ).length,
    wide: entries.filter((entry, index) => index >= WIDE && !entry.isCurrent)
      .length,
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

{#if entries.length > 0 || tallies.length > 0}
  <nav
    class="grid gap-1.5 text-[0.8125rem]/snug md:col-span-2"
    aria-labelledby="{uid}-title"
  >
    <h2 id="{uid}-title" class="font-bold">
      {m.same_day_title({ place })}
    </h2>
    {#if entries.length > 0}
      <ul class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        {#each entries as entry, index (entry.key)}
          <li
            class={cn("max-inline-full", {
              "max-sm:hidden": !isOpen && index >= NARROW && !entry.isCurrent,
              hidden: !isOpen && index >= WIDE && !entry.isCurrent,
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
        {#if hidden.narrow > 0}
          <li class={cn({ "sm:hidden": hidden.wide === 0 })}>
            <button
              type="button"
              class="font-semibold text-muted-foreground underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-foreground"
              aria-expanded={isOpen}
              onclick={() => (isOpen = !isOpen)}
            >
              {#if isOpen}
                {m.same_day_fewer()}
              {:else}
                <span class="sm:hidden"
                  >{m.same_day_more({ count: count(hidden.narrow) })}</span
                ><span class="max-sm:hidden"
                  >{m.same_day_more({ count: count(hidden.wide) })}</span
                >
              {/if}
            </button>
          </li>
        {/if}
      </ul>
    {/if}
    {#if tallies.length > 0 || day?.part?.path != null}
      <ul
        class={cn("flex flex-wrap items-baseline gap-x-4 gap-y-1", {
          "border-bs border-border pbs-1.5": entries.length > 0,
        })}
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
          <li>
            <a
              class="group inline-flex items-baseline gap-1.5"
              href="{resolve('/[...path]', {
                path: group.path,
              })}{group.anchor == null ? '' : `#${group.anchor}`}"
            >
              <span class="font-figures font-semibold tabular-nums"
                >{count(group.count)}</span
              >
              <span
                class="underline decoration-border underline-offset-4 group-hover:decoration-foreground"
                >{group.label}</span
              >
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </nav>
{/if}
