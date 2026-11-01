<script
  lang="ts"
  generics="Row extends { k: number; name: string; shares: Array<number>; leader: number | null; valid: number; margin: number | null }"
>
  import Name from "#lib/components/name.svelte";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { Windowed } from "#lib/windowed.svelte.js";

  const {
    units,
    isFlat,
    colors,
    titles,
    legend,
  }: {
    units: Array<Row>;
    isFlat: boolean;
    colors: Array<string>;
    titles: Array<string>;
    legend: Array<{ person?: string | null; party?: string | null }>;
  } = $props();

  const windowed = new Windowed(() => ({
    items: units,
    key: (row) => String(row.k),
    size: 31,
  }));
</script>

<table class="border-collapse text-sm/tight inline-full">
  <tbody {@attach windowed.anchor}>
    {#if windowed.before > 0}
      <tr aria-hidden="true">
        <td class="p-0" colspan="5" style:block-size="{windowed.before}px"></td>
      </tr>
    {/if}
    {#each windowed.entries as { value: row, index: at, key } (key)}
      <tr
        {@attach windowed.measure(at)}
        class="border-bs border-border/40 first:border-bs-0 hover:bg-secondary/70"
      >
        <th
          class={cn("py-1.5 pe-2 text-start font-normal", {
            "ps-5": !isFlat,
            "ps-2": isFlat,
          })}
          scope="row"
        >
          <span class="font-medium">{row.name}</span>
          <span
            class="mbs-1.5 flex overflow-hidden rounded-xs bg-muted block-2 sm:hidden"
            aria-hidden="true"
          >
            {#each row.shares as share, index (index)}
              {#if share > 0}
                <span
                  class="shrink-0 bg-(--party) not-last:border-e not-last:border-card"
                  style:--party={colors[index]}
                  style:inline-size="{share * 100}%"
                ></span>
              {/if}
            {/each}
          </span>
        </th>
        <td class="px-2 py-1.5 inline-[34%] max-sm:hidden">
          <span
            class="flex overflow-hidden rounded-xs bg-muted block-3"
            aria-hidden="true"
          >
            {#each row.shares as share, index (index)}
              {#if share > 0}
                <span
                  class="shrink-0 bg-(--party) not-last:border-e not-last:border-card"
                  style:--party={colors[index]}
                  style:inline-size="{share * 100}%"
                ></span>
              {/if}
            {/each}
          </span>
        </td>
        <td class="px-2 py-1.5 whitespace-nowrap">
          {#if row.leader != null}
            <span class="flex items-center gap-1.5">
              <Name
                class="truncate font-semibold max-inline-40"
                label={titles[row.leader] ?? ""}
                color={colors[row.leader]}
                person={legend[row.leader]?.person}
                party={legend[row.leader]?.party}
              />
              <span
                class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                >{percent(row.shares[row.leader] ?? 0)}</span
              >
            </span>
          {:else if row.valid === 0}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{m.places_no_votes()}</span
            >
          {/if}
        </td>
        <td
          class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums max-sm:hidden"
        >
          {row.margin == null ? "" : points(row.margin)}
        </td>
        <td
          class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums"
        >
          {count(row.valid)}
        </td>
      </tr>
    {/each}
    {#if windowed.after > 0}
      <tr aria-hidden="true">
        <td class="p-0" colspan="5" style:block-size="{windowed.after}px"></td>
      </tr>
    {/if}
  </tbody>
</table>
