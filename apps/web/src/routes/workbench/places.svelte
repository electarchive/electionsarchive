<script lang="ts">
  import type { SvelteSet } from "svelte/reactivity";
  import type { orpc } from "#lib/rpc.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  let {
    contest,
    focus,
    picked,
    hovered = $bindable(),
  }: {
    contest: Awaited<ReturnType<typeof orpc.contests.get.call>>;
    focus: { key: string; label: string } | null;
    picked: SvelteSet<string>;
    hovered: string | null;
  } = $props();

  let column = $state<"name" | "share" | "change" | "valid">("name");
  let isDescending = $state(false);
  let isAll = $state(false);

  const at = $derived(
    contest.places?.legend.findIndex((entry) => entry.key === focus?.key) ?? -1,
  );
  const rows = $derived(
    (contest.places?.rows ?? [])
      .map((row) => {
        const share = row.shares[at] ?? 0;
        const earlier = row.earlier?.[at];
        return {
          ...row,
          share,
          change: earlier == null ? null : share - earlier,
          lead:
            row.leader == null
              ? null
              : (contest.places?.legend[row.leader] ?? null),
        };
      })
      .toSorted((a, b) => {
        const first = Number(picked.has(b.name)) - Number(picked.has(a.name));
        if (first !== 0) {
          return first;
        }
        const order =
          column === "name"
            ? a.name.localeCompare(b.name, undefined, { numeric: true })
            : (a[column] ?? -2) - (b[column] ?? -2);
        return isDescending ? -order : order;
      }),
  );
  const visible = $derived(isAll ? rows : rows.slice(0, 100));
  const noun = $derived(
    contest.places?.levels.find((level) => level.code === contest.places?.level)
      ?.noun ?? "",
  );
  const columns = $derived([
    { key: "name" as const, label: noun, isNumeric: false },
    {
      key: "share" as const,
      label: focus?.label ?? m.col_share(),
      isNumeric: true,
    },
    ...(contest.target == null
      ? []
      : [{ key: "change" as const, label: m.col_change(), isNumeric: true }]),
    {
      key: "valid" as const,
      label: m.col_votes(),
      isNumeric: true,
      isWide: true,
    },
  ]);
</script>

{#if contest.places == null || contest.places.rows.length === 0}
  <p class="p-3.5 text-sm text-muted-foreground">{m.workbench_no_places()}</p>
{:else}
  <div class="overflow-x-auto">
    <table class="text-sm inline-full">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          {#each columns as entry (entry.key)}
            <th
              class={cn("px-2.5 font-semibold block-9 sm:px-3.5", {
                "text-end": entry.isNumeric,
                "text-start": !entry.isNumeric,
                "max-sm:hidden": "isWide" in entry,
              })}
              aria-sort={column === entry.key
                ? isDescending
                  ? "descending"
                  : "ascending"
                : undefined}
            >
              <button
                type="button"
                class="truncate capitalize max-inline-32 hover:text-foreground"
                onclick={() => {
                  isDescending =
                    column === entry.key ? !isDescending : entry.isNumeric;
                  column = entry.key;
                }}>{entry.label}</button
              >
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each visible as row (row.k)}
          <tr
            class={cn("cursor-pointer border-be last:border-be-0", {
              "bg-secondary": hovered === row.name,
              "bg-primary/15": picked.has(row.name),
            })}
            onpointerenter={() => (hovered = row.name)}
            onpointerleave={() => (hovered = null)}
            onclick={() => {
              if (picked.has(row.name)) {
                picked.delete(row.name);
              } else {
                picked.add(row.name);
              }
            }}
          >
            <td class="px-2.5 block-8 sm:px-3.5">
              <span class="flex items-center gap-2">
                <input
                  type="checkbox"
                  class="accent-foreground"
                  checked={picked.has(row.name)}
                  aria-label={row.name}
                  onclick={(event) => event.stopPropagation()}
                  onchange={() => {
                    if (picked.has(row.name)) {
                      picked.delete(row.name);
                    } else {
                      picked.add(row.name);
                    }
                  }}
                />
                {#if row.lead != null}
                  <span
                    class="shrink-0 rounded-xs block-2.5 inline-2.5"
                    style:background-color={swatch(
                      row.lead.color,
                      row.lead.key,
                    )}
                    title={row.lead.label}
                  ></span>
                {/if}
                <span class={cn({ "font-semibold": picked.has(row.name) })}
                  >{row.name}</span
                >
              </span>
            </td>
            <td
              class="px-2.5 text-end font-figures text-[0.8125rem] tabular-nums sm:px-3.5"
              >{percent(row.share)}</td
            >
            {#if contest.target != null}
              <td
                class="px-2.5 text-end font-figures text-[0.8125rem] tabular-nums sm:px-3.5"
                >{row.change == null ? "" : points(row.change)}</td
              >
            {/if}
            <td
              class="px-2.5 text-end font-figures text-[0.8125rem] tabular-nums sm:px-3.5 max-sm:hidden"
              >{count(row.valid)}</td
            >
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if rows.length > visible.length}
    <button
      type="button"
      class="justify-self-start px-3.5 text-sm font-semibold underline decoration-border underline-offset-4 block-10 hover:decoration-foreground"
      onclick={() => (isAll = true)}
      >{m.places_show_all({ count: count(rows.length) })}</button
    >
  {/if}
{/if}
