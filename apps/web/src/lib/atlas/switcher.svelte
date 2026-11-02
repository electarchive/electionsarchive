<script lang="ts">
  import { goto } from "$app/navigation";
  import CheckIcon from "@lucide/svelte/icons/check";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery } from "@tanstack/svelte-query";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  let {
    contest,
    whole,
    path,
    unit,
    title,
    params,
    isWide,
    isOpen = $bindable(false),
  }: {
    contest: string;
    whole: string | null;
    path: string;
    unit: string;
    title: string;
    params: Record<string, string | null>;
    isWide: boolean;
    isOpen?: boolean;
  } = $props();

  const UUID = /^[0-9a-f-]{36}$/iu;

  const nav = createQuery(() => ({
    ...orpc.maps.nav.queryOptions({
      input:
        whole == null
          ? { id: contest }
          : { id: contest, whole, anchor: path.split("/").at(-1) ?? "" },
    }),
    staleTime: 10 * 60_000,
    meta: { quiet: true },
  }));

  const series = $derived(nav.data?.series ?? []);
  const at = $derived(series.findIndex((entry) => entry.path === path));
  const earlier = $derived(at > 0 ? (series[at - 1] ?? null) : null);
  const later = $derived(at !== -1 ? (series[at + 1] ?? null) : null);
  const years = $derived.by(() => {
    const seen = new Map<string, number>();
    for (const entry of series) {
      const year = entry.date?.slice(0, 4) ?? "";
      seen.set(year, (seen.get(year) ?? 0) + 1);
    }
    return seen;
  });

  function when(entry: { date: string | null }) {
    if (entry.date == null) {
      return "";
    }
    const year = entry.date.slice(0, 4);
    return (years.get(year) ?? 0) > 1 ? date(entry.date) : year;
  }

  function query(keys: Array<string> | null) {
    const found = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value == null || (keys != null && !keys.includes(key))) {
        continue;
      }
      found.set(key, key === "vs" && UUID.test(value) ? "previous" : value);
    }
    return found.size === 0 ? "" : `?${found}`;
  }

  function step(entry: { path: string }) {
    return `/map/${[entry.path, unit].filter((part) => part !== "").join("/")}${query(null)}`;
  }

  function to(target: string, keys: Array<string>) {
    return `/map/${target}${query(keys)}`;
  }

  export function move(delta: -1 | 1) {
    const entry = delta < 0 ? earlier : later;
    if (entry != null) {
      void goto(step(entry));
    }
  }
</script>

{#snippet label()}
  <span class="truncate">{title}</span>
  <ChevronDownIcon
    class="shrink-0 text-muted-foreground block-4 inline-4"
    aria-hidden="true"
  />
{/snippet}

{#snippet list()}
  <Command.Root loop>
    <Command.Input placeholder={m.atlas_switch_find()} />
    <Command.List
      class={cn({
        "max-block-[min(28rem,calc(var(--bits-popover-content-available-height)-3rem))]":
          isWide,
        "max-block-[60dvh]": !isWide,
      })}
    >
      {#if nav.data == null}
        <Command.Loading>{m.atlas_switch_loading()}</Command.Loading>
      {:else}
        {const data = $derived(nav.data)}
        <Command.Empty>{m.atlas_switch_empty()}</Command.Empty>
        {#if data.races.length > 0}
          <Command.Group heading={data.election.name}>
            {#each data.races as race (race.path)}
              <Command.LinkItem
                href={to(race.path, ["at", "basemap"])}
                value="race|{race.path}"
                keywords={[race.name]}
                aria-current={race.path === path ? "page" : undefined}
                onSelect={() => (isOpen = false)}
              >
                <span class="flex-1 truncate">{race.name}</span>
                {#if race.path === path}
                  <CheckIcon class="shrink-0 block-4 inline-4" />
                {/if}
              </Command.LinkItem>
            {/each}
          </Command.Group>
        {/if}
        {#if series.length > 1}
          <Command.Group heading={m.atlas_switch_series()}>
            {#each series.toReversed() as entry (entry.path)}
              <Command.LinkItem
                href={step(entry)}
                value="series|{entry.path}"
                keywords={[entry.name, entry.date ?? "", entry.winner ?? ""]}
                aria-current={entry.path === path ? "page" : undefined}
                onSelect={() => (isOpen = false)}
              >
                <span
                  class="shrink-0 font-figures tabular-nums inline-[5.5ch]"
                  class:font-semibold={entry.path === path}>{when(entry)}</span
                >
                <span
                  class={cn("shrink-0 rounded-xs block-2.5 inline-2.5", {
                    "bg-muted ring-1 ring-border ring-inset":
                      entry.color == null,
                  })}
                  style:background-color={entry.color == null
                    ? undefined
                    : swatch(entry.color, entry.winner ?? "")}
                ></span>
                <span class="flex-1 truncate text-muted-foreground"
                  >{entry.winner ?? entry.name}</span
                >
                {#if entry.path === path}
                  <CheckIcon class="shrink-0 block-4 inline-4" />
                {/if}
              </Command.LinkItem>
            {/each}
          </Command.Group>
        {/if}
        {#if data.elections.length > 0}
          <Command.Group
            heading={m.atlas_switch_elections({ polity: data.polity.name })}
          >
            {#each data.elections as election (election.path)}
              <Command.LinkItem
                href={to(election.path, ["basemap"])}
                value="election|{election.path}"
                keywords={[election.name, election.date ?? ""]}
                onSelect={() => (isOpen = false)}
              >
                <span class="flex-1 truncate">{election.name}</span>
                {#if election.date != null}
                  <span
                    class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
                    >{date(election.date)}</span
                  >
                {/if}
              </Command.LinkItem>
            {/each}
          </Command.Group>
        {/if}
        <Command.Group heading={m.atlas_switch_countries()}>
          {#each data.polities as polity (polity.path)}
            <Command.LinkItem
              href={to(polity.path, ["basemap"])}
              value="polity|{polity.path}"
              keywords={[polity.name]}
              aria-current={polity.name === data.polity.name
                ? "true"
                : undefined}
              onSelect={() => (isOpen = false)}
            >
              <span class="flex-1 truncate">{polity.name}</span>
              {#if polity.name === data.polity.name}
                <CheckIcon class="shrink-0 block-4 inline-4" />
              {/if}
            </Command.LinkItem>
          {/each}
        </Command.Group>
      {/if}
    </Command.List>
  </Command.Root>
{/snippet}

<div class="flex items-center gap-1.5 min-inline-0">
  <h1 class="flex min-inline-0">
    {#if isWide}
      <Popover.Root bind:open={isOpen}>
        <Popover.Trigger
          class="-mx-1.5 flex items-center gap-1 rounded-sm px-1.5 text-[0.9375rem]/6 font-[720] min-inline-0 hover:bg-secondary aria-expanded:bg-secondary"
          aria-label="{title}: {m.atlas_switch()}"
          title={m.atlas_switch()}
        >
          {@render label()}
        </Popover.Trigger>
        <Popover.Content
          align="start"
          class="gap-0 overflow-hidden p-0 inline-[min(26rem,calc(100vw-2rem))]"
        >
          {@render list()}
        </Popover.Content>
      </Popover.Root>
    {:else}
      <Drawer.Root bind:open={isOpen}>
        <Drawer.Trigger
          class="-mx-1.5 flex items-center gap-1 rounded-sm px-1.5 text-[0.9375rem]/6 font-[720] min-inline-0 hover:bg-secondary aria-expanded:bg-secondary"
          aria-label="{title}: {m.atlas_switch()}"
        >
          {@render label()}
        </Drawer.Trigger>
        <Drawer.Content>
          <Drawer.Title class="px-4 pbs-3 text-base font-semibold"
            >{m.atlas_switch()}</Drawer.Title
          >
          <div class="px-2 pbs-2 pbe-[calc(1rem+env(safe-area-inset-bottom))]">
            {@render list()}
          </div>
        </Drawer.Content>
      </Drawer.Root>
    {/if}
  </h1>
  {#if series.length > 1 && at !== -1}
    <div
      class="flex shrink-0 items-center rounded-sm ring-1 ring-border block-6"
    >
      {@render arrow(earlier, true)}
      <span
        class="px-1 font-figures text-xs font-semibold tabular-nums"
        aria-current="page">{when(series[at] ?? { date: null })}</span
      >
      {@render arrow(later, false)}
    </div>
  {/if}
</div>

{#snippet arrow(
  entry: { name: string; path: string } | null,
  isEarlier: boolean,
)}
  {#if entry == null}
    <span
      class="grid place-items-center text-muted-foreground/40 block-6 inline-6"
      aria-label={isEarlier ? m.atlas_no_earlier() : m.atlas_no_later()}
      role="img"
    >
      {#if isEarlier}<ChevronLeftIcon
          class="block-3.5 inline-3.5"
        />{:else}<ChevronRightIcon class="block-3.5 inline-3.5" />{/if}
    </span>
  {:else}
    {const hint = $derived(
      isEarlier
        ? m.atlas_earlier({ name: entry.name })
        : m.atlas_later({ name: entry.name }),
    )}
    <a
      href={step(entry)}
      class="grid place-items-center text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
      aria-label={hint}
      title={hint}
    >
      {#if isEarlier}<ChevronLeftIcon
          class="block-3.5 inline-3.5"
        />{:else}<ChevronRightIcon class="block-3.5 inline-3.5" />{/if}
    </a>
  {/if}
{/snippet}
