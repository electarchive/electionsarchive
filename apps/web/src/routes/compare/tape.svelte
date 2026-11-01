<script lang="ts">
  import { cn } from "#lib/utils.js";

  const {
    names,
    colors,
    rows,
  }: {
    names: [string, string];
    colors: [string, string];
    rows: Array<{
      label: string;
      values: [number | null, number | null];
      text: [string, string];
      isLowBetter?: boolean;
      isPlain?: boolean;
    }>;
  } = $props();

  const lead = (values: [number | null, number | null], isLow: boolean) => {
    const [a, b] = values;
    if (a == null || b == null || a === b) {
      return -1;
    }
    return a > b !== isLow ? 0 : 1;
  };
</script>

<div class="grid gap-px text-[0.8125rem]">
  <div
    class="grid grid-cols-[minmax(0,1fr)_minmax(6.5rem,auto)_minmax(0,1fr)] items-end gap-x-3 pbe-1.5"
  >
    {#each names as name, side (side)}
      <span
        class={cn("flex items-center gap-1.5 font-bold min-inline-0", {
          "col-start-1 justify-end text-end": side === 0,
          "col-start-3 justify-start": side === 1,
        })}
      >
        <span
          class={cn("shrink-0 block-2 inline-2", {
            "order-last rounded-full": side === 0,
          })}
          style:background-color={colors[side]}
          aria-hidden="true"
        ></span>
        <span class="truncate">{name}</span>
      </span>
    {/each}
  </div>
  {#each rows as row (row.label)}
    {const winner = $derived(lead(row.values, row.isLowBetter === true))}
    {const reach = $derived(
      Math.max(...row.values.map((value) => Math.abs(value ?? 0))),
    )}
    <div
      class="grid grid-cols-[minmax(0,1fr)_minmax(6.5rem,auto)_minmax(0,1fr)] items-center gap-x-3 border-bs border-border block-7"
    >
      {#each row.values as value, side (side)}
        <span
          class={cn("flex items-center gap-2 min-inline-0", {
            "col-start-1 flex-row-reverse": side === 0,
            "col-start-3": side === 1,
          })}
        >
          <span
            class={cn("shrink-0 font-figures tabular-nums", {
              "font-bold": winner === side,
              "text-muted-foreground": winner !== side && winner !== -1,
            })}>{row.text[side]}</span
          >
          {#if row.isPlain !== true && value != null && reach > 0}
            <span class="flex flex-1 block-2.5" class:justify-end={side === 0}>
              <span
                class="block-full"
                style:inline-size="{(Math.abs(value) / reach) * 100}%"
                style:background-color={colors[side]}
                style:opacity={winner === side || winner === -1 ? 1 : 0.35}
              ></span>
            </span>
          {/if}
        </span>
      {/each}
      <span
        class="col-start-2 row-start-1 text-center text-xs text-pretty text-muted-foreground"
        >{row.label}</span
      >
    </div>
  {/each}
</div>
