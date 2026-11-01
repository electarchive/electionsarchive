<script lang="ts">
  import { lead } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { tails, other }: { tails: Array<[string, number]>; other: number } =
    $props();

  const rows = $derived([
    ...tails
      .slice(0, 12)
      .map(([state, share]) => ({
        label: state.toUpperCase(),
        share,
        isOther: false,
      })),
    { label: m.method_order_other(), share: other, isOther: true },
  ]);
  const top = $derived(Math.max(...rows.map((row) => row.share)));
  const shown = (share: number) =>
    lead(share, Math.round(share * 1000) % 10 === 0 ? 0 : 1);
</script>

<figure class="grid gap-3">
  <figcaption class="text-sm font-semibold">
    {m.method_order_chart()}
  </figcaption>
  <ol class="grid gap-1">
    {#each rows as row (row.label)}
      <li
        class="grid grid-cols-[minmax(2.5rem,auto)_minmax(0,1fr)_2.5rem] items-center gap-2.5 text-sm"
      >
        <span
          class={cn({
            "text-muted-foreground": row.isOther,
            "font-semibold": !row.isOther,
          })}>{row.label}</span
        >
        <span class="block bg-muted block-3.5">
          <span
            class={cn("block block-full", {
              "bg-muted-foreground/50": row.isOther,
              "bg-foreground": !row.isOther,
            })}
            style:inline-size="{(row.share / top) * 100}%"
          ></span>
        </span>
        <span class="text-end font-figures tabular-nums"
          >{shown(row.share)}</span
        >
      </li>
    {/each}
  </ol>
</figure>
