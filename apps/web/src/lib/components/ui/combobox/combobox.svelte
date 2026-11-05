<script
  lang="ts"
  generics="Item extends { value: string; label: string; detail?: string; group?: string; keywords?: Array<string> }"
>
  import type { Snippet } from "svelte";
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
  import { computeCommandScore } from "bits-ui";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { cn } from "#lib/utils.js";

  let {
    items,
    value = $bindable(null),
    label,
    placeholder,
    search,
    empty,
    clearable = false,
    class: className,
    wide = false,
    row,
    selection,
    trigger,
    onValueChange,
  }: {
    items: Array<Item>;
    value?: string | null;
    label: string;
    placeholder: string;
    search: string;
    empty: string;
    clearable?: boolean;
    class?: string;
    wide?: boolean;
    row?: Snippet<[Item]>;
    selection?: Snippet<[Item]>;
    trigger?: Snippet<[Record<string, unknown>]>;
    onValueChange?: (value: string | null) => void;
  } = $props();

  const WINDOW = 40;

  let open = $state(false);
  let query = $state("");
  let shown = $state(WINDOW);

  const narrow = $derived(isNarrowLayout());
  const selected = $derived(items.find((item) => item.value === value));
  const needle = $derived(query.trim());
  const matches = $derived.by(() => {
    if (needle === "") {
      return items;
    }
    const scored = items.flatMap((item) => {
      const score = computeCommandScore(item.value, needle, [
        item.label,
        ...(item.detail == null ? [] : [item.detail]),
        ...(item.keywords ?? []),
      ]);
      return score > 0 ? [{ item, score }] : [];
    });
    return scored.toSorted((a, b) => b.score - a.score).map(({ item }) => item);
  });
  const lifted = $derived(
    needle === "" && selected != null && items.indexOf(selected) >= WINDOW
      ? selected
      : null,
  );
  const groups = $derived.by(() => {
    const grouped = new Map<string, Array<Item>>(
      lifted == null ? [] : [["", [lifted]]],
    );
    for (const item of matches.slice(0, shown)) {
      if (item === lifted) {
        continue;
      }
      const group = grouped.get(item.group ?? "");
      if (group == null) {
        grouped.set(item.group ?? "", [item]);
      } else {
        group.push(item);
      }
    }
    return [...grouped];
  });

  const reset = (isOpen: boolean) => {
    if (isOpen) {
      query = "";
      shown = WINDOW;
    }
  };

  const growAtEnd = (end: HTMLElement) => {
    void shown;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.at(-1)?.isIntersecting === true) {
          shown += WINDOW;
        }
      },
      { root: end.closest('[data-slot="command-list"]'), rootMargin: "240px" },
    );
    observer.observe(end);
    return () => {
      observer.disconnect();
    };
  };
</script>

{#snippet field(props: Record<string, unknown>)}
  <button
    {...props}
    type="button"
    data-slot="combobox-trigger"
    class={cn(
      "flex h-8 w-full min-w-0 field items-center gap-2 ps-2.5 pe-2 text-start",
      className,
    )}
  >
    <span class="sr-only">{label}:</span>
    {#if selected != null && selection != null}
      {@render selection(selected)}
    {:else if selected != null}
      <span class="min-w-0 flex-1 truncate" title={selected.label}
        >{selected.label}</span
      >
    {:else}
      <span class="min-w-0 flex-1 truncate text-muted-foreground">
        {placeholder}
      </span>
    {/if}
    <ChevronsUpDownIcon class="size-3.5 shrink-0 text-muted-foreground" />
  </button>
{/snippet}

{#snippet picker()}
  <Command.Root
    value={value ?? ""}
    loop
    disableInitialScroll
    shouldFilter={false}
  >
    <Command.Input
      placeholder={search}
      bind:value={
        () => query,
        (next) => {
          query = next;
          shown = WINDOW;
        }
      }
    />
    <Command.List
      class="in-data-[slot=drawer-content]:max-h-[60dvh]"
      {@attach (list) => {
        const checked = list.querySelector('[data-checked="true"]');
        if (checked != null) {
          list.scrollTop +=
            checked.getBoundingClientRect().top -
            list.getBoundingClientRect().top -
            (list.clientHeight - checked.clientHeight) / 2;
        }
      }}
    >
      <Command.Empty>{empty}</Command.Empty>
      {#each groups as [group, entries] (group)}
        <Command.Group heading={group || undefined}>
          {#each entries as item (item.value)}
            <Command.Item
              value={item.value}
              data-checked={item.value === value}
              onSelect={() => {
                value = clearable && value === item.value ? null : item.value;
                onValueChange?.(value);
                open = false;
              }}
            >
              {#if row == null}
                <span class="min-w-0 flex-1 text-pretty">{item.label}</span>
                {#if item.detail != null}
                  <span
                    class="shrink-0 font-figures text-xs text-muted-foreground"
                  >
                    {item.detail}
                  </span>
                {/if}
              {:else}
                {@render row(item)}
              {/if}
            </Command.Item>
          {/each}
        </Command.Group>
      {/each}
      {#if shown < matches.length}
        <div aria-hidden="true" class="h-px" {@attach growAtEnd}></div>
      {/if}
    </Command.List>
  </Command.Root>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open onOpenChange={reset}>
    <Drawer.Trigger>
      {#snippet child({ props })}
        {@render (trigger ?? field)(props)}
      {/snippet}
    </Drawer.Trigger>
    <Drawer.Content>
      <Drawer.Header class="sr-only">
        <Drawer.Title>{label}</Drawer.Title>
      </Drawer.Header>
      <div class="pt-2">
        {@render picker()}
      </div>
    </Drawer.Content>
  </Drawer.Root>
{:else}
  <Popover.Root bind:open onOpenChange={reset}>
    <Popover.Trigger>
      {#snippet child({ props })}
        {@render (trigger ?? field)(props)}
      {/snippet}
    </Popover.Trigger>
    <Popover.Content
      align="start"
      class={cn("gap-0 overflow-hidden p-0", {
        "w-(--bits-popover-anchor-width) min-w-64": !wide,
        "w-[min(34rem,calc(100vw-2rem))]": wide,
      })}
    >
      {@render picker()}
    </Popover.Content>
  </Popover.Root>
{/if}
