<script lang="ts">
  import ChevronUpIcon from "@lucide/svelte/icons/chevron-up";
  import type { Lens } from "#lib/atlas/color.js";
  import type { Hit } from "#lib/atlas/stage.svelte.js";
  import { tone } from "#lib/atlas/color.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";

  const {
    entries,
    lens,
    labels,
    picked,
    isWide,
    onhover,
    onpick,
  }: {
    entries: Array<{ hit: Hit; group: string; code: string | null }>;
    lens: Lens;
    labels: Map<string, string>;
    picked: Array<number>;
    isWide: boolean;
    onhover: (hit: Hit | null, point: { x: number; y: number } | null) => void;
    onpick: (hit: Hit, isMany: boolean) => void;
  } = $props();

  let listing = $state<string | null>(null);

  const headings: Record<string, () => string> = {
    territory: m.map_outlying,
    overseas: m.map_overseas,
    abroad: m.map_abroad,
  };

  const segments = $derived(
    ["territory", "overseas", "abroad"].flatMap((group) => {
      const found = entries
        .filter((entry) => entry.group === group)
        .map(({ hit, code }) => {
          const { set, i } = hit;
          const leader = set.places.leader[i];
          return {
            hit,
            k: set.places.k[i] ?? 0,
            code,
            name: set.places.name[i] ?? "",
            fill: tone(set, i, lens),
            winner:
              leader == null
                ? null
                : (labels.get(set.legend[leader]?.key ?? "") ??
                  set.legend[leader]?.label ??
                  null),
          };
        })
        .toSorted((a, b) =>
          (a.code ?? a.name).localeCompare(b.code ?? b.name, getLocale(), {
            numeric: true,
          }),
        );
      if (found.length === 0) {
        return [];
      }
      const blocks = Map.groupBy(found, (entry) => {
        const cut = entry.name.lastIndexOf(", ");
        const prefix = cut > 0 ? entry.name.slice(0, cut) : "";
        return prefix !== "" &&
          found.filter((other) => other.name.startsWith(`${prefix}, `)).length >
            1
          ? prefix
          : "";
      })
        .entries()
        .map(([header, lines]) => ({
          header,
          lines: lines.map((entry) => ({
            entry,
            label:
              header === ""
                ? entry.name
                : entry.name.slice(entry.name.lastIndexOf(", ") + 2),
          })),
        }))
        .toArray();
      return [
        {
          group,
          heading: headings[group]?.() ?? "",
          entries: found,
          blocks,
          isInline:
            found.length === 1 ||
            (found.every((entry) => entry.code != null) &&
              found.length <= (isWide ? 12 : 6)),
        },
      ];
    }),
  );
  const chosen = $derived(new Set(picked));

  function spot(target: HTMLElement) {
    const bounds = target
      .closest("[data-outlying]")
      ?.parentElement?.parentElement?.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    return bounds == null
      ? null
      : {
          x: rect.left - bounds.left + rect.width / 2,
          y: rect.top - bounds.top,
        };
  }
</script>

<div
  class="pointer-events-none flex flex-wrap-reverse justify-end gap-1.5 max-md:justify-start"
  data-outlying
>
  {#each segments as segment (segment.group)}
    {#if segment.isInline}
      <div
        class="pointer-events-auto flex overflow-hidden rounded-sm bg-card/95 shadow-menu ring-1 ring-border"
        role="group"
        aria-label={segment.heading}
      >
        {#each segment.entries as entry (entry.k)}
          <button
            type="button"
            class={cn(
              "flex shrink-0 items-center gap-1.5 border-s border-border px-2 text-xs font-semibold whitespace-nowrap block-7 first:border-s-0 hover:bg-secondary pointer-coarse:block-10",
              { "bg-secondary": chosen.has(entry.k) },
            )}
            aria-label={entry.name}
            aria-pressed={chosen.has(entry.k)}
            onpointerenter={(event) => {
              if (event.pointerType === "mouse") {
                onhover(entry.hit, spot(event.currentTarget));
              }
            }}
            onpointerleave={() => onhover(null, null)}
            onfocus={(event) => onhover(entry.hit, spot(event.currentTarget))}
            onblur={() => onhover(null, null)}
            onclick={(event) =>
              onpick(
                entry.hit,
                event.shiftKey || event.metaKey || event.ctrlKey,
              )}
          >
            <span
              class="rounded-xs bg-(--party) block-2.5 inline-2.5"
              style:--party={entry.fill}
              aria-hidden="true"
            ></span>
            {entry.code ?? entry.name}
          </button>
        {/each}
      </div>
    {:else if isWide}
      <Popover.Root
        bind:open={
          () => listing === segment.group,
          (value: boolean) => {
            listing = value ? segment.group : null;
          }
        }
      >
        <Popover.Trigger
          class="pointer-events-auto flex items-center gap-2 rounded-sm bg-card/95 ps-2.5 pe-2 text-xs font-semibold shadow-menu ring-1 ring-border block-7 hover:bg-secondary data-[state=open]:bg-secondary"
        >
          {@render summary(segment.entries, segment.heading)}
        </Popover.Trigger>
        <Popover.Content
          side="top"
          align="end"
          class="gap-0 overflow-y-auto p-1 inline-80 max-block-[min(24rem,60dvh)]"
        >
          {@render roster(segment.blocks, false)}
        </Popover.Content>
      </Popover.Root>
    {:else}
      <Drawer.Root
        bind:open={
          () => listing === segment.group,
          (value: boolean) => {
            listing = value ? segment.group : null;
          }
        }
      >
        <Drawer.Trigger
          class="pointer-events-auto flex items-center gap-2 rounded-sm bg-card/95 ps-2.5 pe-2 text-xs font-semibold shadow-menu ring-1 ring-border block-10 hover:bg-secondary data-[state=open]:bg-secondary"
        >
          {@render summary(segment.entries, segment.heading)}
        </Drawer.Trigger>
        <Drawer.Content>
          <Drawer.Header
            class="flex-row items-baseline justify-between px-4 pbs-3 pbe-1"
          >
            <Drawer.Title class="text-base">{segment.heading}</Drawer.Title>
            <span
              class="font-figures text-sm text-muted-foreground tabular-nums"
              >{count(segment.entries.length)}</span
            >
          </Drawer.Header>
          <div
            class="overflow-y-auto px-2 pbe-[max(1rem,env(safe-area-inset-bottom))] max-block-[60dvh]"
          >
            {@render roster(segment.blocks, true)}
          </div>
        </Drawer.Content>
      </Drawer.Root>
    {/if}
  {/each}
</div>

{#snippet summary(entries: Array<{ k: number; fill: string }>, heading: string)}
  {heading}
  <span class="flex gap-0.5" aria-hidden="true">
    {#each entries.slice(0, 12) as entry (entry.k)}
      <span
        class="rounded-[1px] bg-(--party) block-2.5 inline-1.5"
        style:--party={entry.fill}
      ></span>
    {/each}
  </span>
  <span class="font-figures font-normal text-muted-foreground tabular-nums"
    >{count(entries.length)}</span
  >
  <ChevronUpIcon
    class="text-muted-foreground block-3.5 inline-3.5"
    aria-hidden="true"
  />
{/snippet}

{#snippet roster(blocks: (typeof segments)[number]["blocks"], isSheet: boolean)}
  {#each blocks as block (block.header)}
    <div
      class={cn({
        "not-first:mbs-1 not-first:border-bs not-first:border-border":
          block.header !== "",
      })}
    >
      {#if block.header !== ""}
        <p
          class={cn("px-2 pbs-2 pbe-0.5 text-xs text-muted-foreground", {
            "text-[0.8125rem]": isSheet,
          })}
        >
          {block.header}
        </p>
      {/if}
      {#each block.lines as line (line.entry.k)}
        <button
          type="button"
          class={cn(
            "flex items-center gap-2.5 rounded-xs px-2 py-1.5 text-start inline-full hover:bg-secondary focus-visible:bg-secondary",
            { "py-2.5": isSheet },
            { "bg-secondary": chosen.has(line.entry.k) },
          )}
          aria-pressed={chosen.has(line.entry.k)}
          onclick={(event) => {
            listing = null;
            onpick(
              line.entry.hit,
              event.shiftKey || event.metaKey || event.ctrlKey,
            );
          }}
        >
          <span
            class="mbs-1 shrink-0 self-start rounded-xs bg-(--party) block-2.5 inline-2.5"
            style:--party={line.entry.fill}
            aria-hidden="true"
          ></span>
          <span class="grid flex-1 gap-0.5 min-inline-0">
            <span
              class="text-[0.8125rem] font-semibold text-pretty first-letter:uppercase"
              >{line.label}</span
            >
            {#if line.entry.winner != null}
              <span class="truncate text-xs text-muted-foreground"
                >{line.entry.winner}</span
              >
            {/if}
          </span>
        </button>
      {/each}
    </div>
  {/each}
{/snippet}
