<script lang="ts">
  import { resolve } from "$app/paths";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import * as Command from "#lib/components/ui/command/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { percent } from "#lib/format.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const {
    source,
    noun,
    current,
  }: {
    source:
      | NonNullable<Awaited<ReturnType<typeof orpc.contests.places.call>>>
      | { contest: string; unit: string | null; level: string };
    noun: string;
    current: string | null;
  } = $props();

  let open = $state(false);
  let fetched = $state<{
    key: string;
    places: Awaited<ReturnType<typeof orpc.contests.places.call>>;
  } | null>(null);
  let failed = $state(false);

  const key = $derived(
    "rows" in source
      ? null
      : `${source.contest}/${source.unit ?? ""}/${source.level}`,
  );
  const places = $derived(
    "rows" in source ? source : fetched?.key === key ? fetched.places : null,
  );
  const isLoading = $derived(!("rows" in source) && fetched?.key !== key);
  const narrow = $derived(isNarrowLayout());
  const label = $derived(
    current == null ? m.scope_below({ noun }) : m.siblings_open({ noun }),
  );

  const toggle = (value: boolean) => {
    open = value;
    if (!value || "rows" in source || key == null || fetched?.key === key) {
      return;
    }
    failed = false;
    const asked = key;
    orpc.contests.places
      .call({
        id: source.contest,
        unit: source.unit ?? undefined,
        by: source.level,
      })
      .then((result) => {
        fetched = { key: asked, places: result };
      })
      .catch(() => {
        failed = true;
      });
  };
</script>

{#snippet picker()}
  <Command.Root value={current ?? ""} loop>
    <Command.Input placeholder={m.siblings_search()} />
    <Command.List
      class="in-data-[slot=drawer-content]:max-block-[60dvh]"
      {@attach (list: HTMLElement) => {
        list
          .querySelector('[data-checked="true"]')
          ?.scrollIntoView({ block: "center" });
      }}
    >
      {#if isLoading || places == null}
        <Command.Loading>
          <p class="px-2 py-3 text-sm text-muted-foreground">
            {failed || !isLoading ? m.siblings_empty() : m.siblings_loading()}
          </p>
        </Command.Loading>
      {:else}
        <Command.Empty>{m.siblings_empty()}</Command.Empty>
        {#each places.rows as row (row.path ?? row.name)}
          {#if row.path != null}
            <Command.LinkItem
              href={resolve("/[...path]", { path: row.path })}
              value={row.name}
              data-checked={row.name === current}
              onSelect={() => toggle(false)}
            >
              <span
                class="shrink-0 rounded-xs block-2.5 inline-2.5"
                style:background-color={row.leader == null
                  ? "transparent"
                  : swatch(
                      places.legend[row.leader]?.color,
                      places.legend[row.leader]?.key ?? "",
                    )}
              ></span>
              <span class="flex-1 truncate min-inline-0">{row.name}</span>
              {#if row.leader != null}
                <span class="shrink-0 font-mono text-xs text-muted-foreground"
                  >{places.legend[row.leader]?.short ??
                    places.legend[row.leader]?.label}
                  {percent(row.shares[row.leader] ?? 0)}</span
                >
              {/if}
            </Command.LinkItem>
          {/if}
        {/each}
      {/if}
    </Command.List>
  </Command.Root>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open={() => open, toggle}>
    <Drawer.Trigger
      class={cn(
        "rounded-xs text-muted-foreground block-6 hover:bg-secondary hover:text-foreground data-[state=open]:bg-secondary data-[state=open]:text-foreground",
        {
          "ms-0.5 grid place-items-center inline-5": current != null,
          "flex items-center gap-1 ps-1.5 pe-1": current == null,
        },
      )}
      aria-label={label}
    >
      {#if current == null}
        <span class="first-letter:uppercase">{noun}</span>
      {/if}
      <ChevronDownIcon class="block-3.5 inline-3.5" />
    </Drawer.Trigger>
    <Drawer.Content>
      <Drawer.Header class="sr-only">
        <Drawer.Title>{label}</Drawer.Title>
      </Drawer.Header>
      <div class="pbs-2">
        {@render picker()}
      </div>
    </Drawer.Content>
  </Drawer.Root>
{:else}
  <Popover.Root bind:open={() => open, toggle}>
    <Popover.Trigger
      class={cn(
        "rounded-xs text-muted-foreground block-6 hover:bg-secondary hover:text-foreground data-[state=open]:bg-secondary data-[state=open]:text-foreground",
        {
          "ms-0.5 grid place-items-center inline-5": current != null,
          "flex items-center gap-1 ps-1.5 pe-1": current == null,
        },
      )}
      aria-label={label}
    >
      {#if current == null}
        <span class="first-letter:uppercase">{noun}</span>
      {/if}
      <ChevronDownIcon class="block-3.5 inline-3.5" />
    </Popover.Trigger>
    <Popover.Content align="start" class="gap-0 overflow-hidden p-0 inline-80">
      {@render picker()}
    </Popover.Content>
  </Popover.Root>
{/if}
