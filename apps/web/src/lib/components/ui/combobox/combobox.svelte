<script lang="ts">
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
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
    onValueChange,
  }: {
    items: Array<{
      value: string;
      label: string;
      detail?: string;
      group?: string;
      keywords?: Array<string>;
    }>;
    value?: string | null;
    label: string;
    placeholder: string;
    search: string;
    empty: string;
    clearable?: boolean;
    class?: string;
    onValueChange?: (value: string | null) => void;
  } = $props();

  let open = $state(false);

  const narrow = $derived(isNarrowLayout());
  const selected = $derived(items.find((item) => item.value === value));
  const groups = $derived.by(() => {
    const grouped = new Map<string, typeof items>();
    for (const item of items) {
      const group = grouped.get(item.group ?? "");
      if (group == null) {
        grouped.set(item.group ?? "", [item]);
      } else {
        group.push(item);
      }
    }
    return [...grouped];
  });
</script>

{#snippet trigger(props: Record<string, unknown>)}
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
    {#if selected != null}
      <span class="min-w-0 flex-1 truncate">{selected.label}</span>
      {#if selected.detail != null}
        <span class="shrink-0 font-mono text-xs text-muted-foreground">
          {selected.detail}
        </span>
      {/if}
    {:else}
      <span class="min-w-0 flex-1 truncate text-muted-foreground">
        {placeholder}
      </span>
    {/if}
    <ChevronsUpDownIcon class="size-3.5 shrink-0 text-muted-foreground" />
  </button>
{/snippet}

{#snippet picker()}
  <Command.Root value={value ?? ""} loop>
    <Command.Input placeholder={search} />
    <Command.List
      class="in-data-[slot=drawer-content]:max-h-[60dvh]"
      {@attach (list) => {
        list
          .querySelector('[data-checked="true"]')
          ?.scrollIntoView({ block: "nearest" });
      }}
    >
      <Command.Empty>{empty}</Command.Empty>
      {#each groups as [group, entries] (group)}
        <Command.Group heading={group || undefined}>
          {#each entries as item (item.value)}
            <Command.Item
              value={item.value}
              keywords={[
                item.label,
                ...(item.detail == null ? [] : [item.detail]),
                ...(item.keywords ?? []),
              ]}
              data-checked={item.value === value}
              onSelect={() => {
                value = clearable && value === item.value ? null : item.value;
                onValueChange?.(value);
                open = false;
              }}
            >
              <span class="min-w-0 flex-1 truncate">{item.label}</span>
              {#if item.detail != null}
                <span class="shrink-0 font-mono text-xs text-muted-foreground">
                  {item.detail}
                </span>
              {/if}
            </Command.Item>
          {/each}
        </Command.Group>
      {/each}
    </Command.List>
  </Command.Root>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open>
    <Drawer.Trigger>
      {#snippet child({ props })}
        {@render trigger(props)}
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
  <Popover.Root bind:open>
    <Popover.Trigger>
      {#snippet child({ props })}
        {@render trigger(props)}
      {/snippet}
    </Popover.Trigger>
    <Popover.Content
      align="start"
      class="w-(--bits-popover-anchor-width) min-w-64 gap-0 overflow-hidden p-0"
    >
      {@render picker()}
    </Popover.Content>
  </Popover.Root>
{/if}
