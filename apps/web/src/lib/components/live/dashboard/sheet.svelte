<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Seat } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";

  let {
    isOpen = $bindable(false),
    title,
    note,
    seats,
    name,
    href,
    shade,
    line,
    summary,
  }: {
    isOpen?: boolean;
    title: string;
    note: string;
    seats: Array<Seat>;
    name: (seat: Seat) => string;
    href: (seat: Seat) => string;
    shade: (seat: Seat) => string;
    line: Snippet<[Summary | undefined]>;
    summary: Snippet;
  } = $props();
</script>

<Drawer.Root bind:open={isOpen}>
  <Drawer.Content>
    <div class="grid gap-4 px-4 pbs-3 pbe-6">
      <Drawer.Header class="flex-row items-baseline justify-between gap-3 p-0">
        <Drawer.Title class="heading text-lg!">{title}</Drawer.Title>
        <p class="text-xs text-muted-foreground">{note}</p>
      </Drawer.Header>
      {@render summary()}
      {#if isOpen}
        <ol class="grid border-bs border-border">
          {#each seats as seat (seat.summary.race.id)}
            <li>
              <a
                href={href(seat)}
                class="grid grid-cols-[0.75rem_minmax(0,1fr)_auto] items-center gap-3 border-be border-border py-2.5 text-sm hover:bg-muted"
              >
                <span
                  class="rounded-xs block-3"
                  style:background-color={shade(seat)}
                ></span>
                <span class="truncate font-semibold">{name(seat)}</span>
                <span class="justify-self-end text-[0.8125rem]"
                  >{@render line(seat.summary)}</span
                >
              </a>
            </li>
          {/each}
        </ol>
      {/if}
    </div>
  </Drawer.Content>
</Drawer.Root>
