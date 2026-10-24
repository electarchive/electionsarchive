<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteMap } from "svelte/reactivity";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id, within }: { id: string; within?: string } = $props();

  const uid = $props.id();

  const gains = createQuery(() =>
    orpc.seats.gains.queryOptions({ input: { id, within } }),
  );

  let picked = $state<{ to: string; from: string } | null>(null);
  let isAll = $state(false);

  const sheet = $derived.by(() => {
    const data = gains.data;
    if (data == null) {
      return null;
    }
    const fold = (key: string) =>
      key.startsWith("person:") ? "independent" : key;
    const info = new Map(data.parties.map((party) => [party.key, party]));
    const label = (key: string) =>
      key === "independent"
        ? m.seats_independents()
        : (info.get(key)?.short ?? info.get(key)?.label ?? "");
    const color = (key: string) =>
      key === "independent"
        ? swatch(null, "")
        : swatch(info.get(key)?.color ?? null, `party:${key}`);
    const moved = data.flows.filter((flow) => flow.to !== flow.from);
    const cells = new SvelteMap<string, number>();
    const gained = new SvelteMap<string, number>();
    const lost = new SvelteMap<string, number>();
    for (const flow of moved) {
      const to = fold(flow.to);
      const from = fold(flow.from);
      if (to === from) {
        continue;
      }
      cells.set(
        `${to}|${from}`,
        (cells.get(`${to}|${from}`) ?? 0) + flow.seats,
      );
      gained.set(to, (gained.get(to) ?? 0) + flow.seats);
      lost.set(from, (lost.get(from) ?? 0) + flow.seats);
    }
    let changed = 0;
    let peak = 0;
    for (const [, seats] of cells) {
      changed += seats;
      peak = Math.max(peak, seats);
    }
    const rows = [...gained.entries()]
      .toSorted((a, b) => b[1] - a[1])
      .map(([key, seats]) => ({
        key,
        label: label(key),
        color: color(key),
        seats,
      }));
    const columns = [...lost.entries()]
      .toSorted((a, b) => b[1] - a[1])
      .map(([key, seats]) => ({
        key,
        label: label(key),
        color: color(key),
        seats,
      }));
    return {
      ...data,
      changed,
      peak,
      cells,
      rows,
      columns,
      seats: data.changed
        .map((seat) => ({
          ...seat,
          to: fold(seat.to),
          from: fold(seat.from),
          toLabel: label(fold(seat.to)),
          fromLabel: label(fold(seat.from)),
          toColor: color(fold(seat.to)),
          fromColor: color(fold(seat.from)),
        }))
        .filter((seat) => seat.to !== seat.from),
    };
  });

  const listed = $derived(
    (sheet?.seats ?? []).filter(
      (seat) =>
        picked == null || (seat.to === picked.to && seat.from === picked.from),
    ),
  );
</script>

{#if sheet != null && sheet.changed > 0}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">{m.gains_title()}</h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.gains_lede({
          changed: count(sheet.changed),
          compared: count(sheet.compared),
          year: String(sheet.since),
        })}
        {#if sheet.compared < sheet.total}
          {m.gains_partial({
            compared: count(sheet.compared),
            total: count(sheet.total),
          })}
        {/if}
      </p>
    </div>
    <div class="relative flex overflow-x-auto">
      <table class="border-collapse text-sm tabular-nums">
        <thead>
          <tr>
            <th
              scope="col"
              class="sticky inset-s-0 bg-card py-1.5 pe-3 text-start text-xs font-semibold text-muted-foreground"
            >
              {m.gains_corner()}
            </th>
            {#each sheet.columns as column (column.key)}
              <th scope="col" class="px-0.5 py-1.5 text-end">
                <span
                  class="inline-flex items-center gap-1.5 px-1.5 text-xs font-semibold whitespace-nowrap"
                >
                  <span
                    class="shrink-0 rounded-xs block-2 inline-2"
                    style:background={column.color}
                  ></span>
                  <span class="sr-only">{m.gains_from()}</span>
                  {column.label}
                </span>
              </th>
            {/each}
            <th
              scope="col"
              class="py-1.5 ps-3 text-end text-xs font-semibold text-muted-foreground"
            >
              {m.gains_gains()}
            </th>
          </tr>
        </thead>
        <tbody>
          {#each sheet.rows as row (row.key)}
            <tr class="border-bs border-border/60">
              <th
                scope="row"
                class="sticky inset-s-0 bg-card py-1 pe-3 text-start font-normal whitespace-nowrap"
              >
                <span class="inline-flex items-center gap-1.5">
                  <span
                    class="shrink-0 rounded-xs block-2 inline-2"
                    style:background={row.color}
                  ></span>
                  {row.label}
                </span>
              </th>
              {#each sheet.columns as column (column.key)}
                {const cell = $derived(
                  sheet.cells.get(`${row.key}|${column.key}`) ?? 0,
                )}
                {const fill = $derived(
                  `color-mix(in oklab, ${row.color} ${Math.round(14 + (cell / Math.max(1, sheet.peak)) * 66)}%, var(--card))`,
                )}
                {const isPicked = $derived(
                  picked?.to === row.key && picked.from === column.key,
                )}
                <td class="p-0.5">
                  {#if cell === 0}
                    <span
                      class="grid place-items-end px-2 font-figures text-[0.8125rem] text-muted-foreground/50 block-7 inline-12"
                      >·</span
                    >
                  {:else}
                    <button
                      type="button"
                      class={cn(
                        "grid place-items-end rounded-xs px-2 font-figures text-[0.8125rem] block-7 inline-12 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-foreground",
                        { "ring-2 ring-foreground": isPicked },
                      )}
                      style:background={fill}
                      style:color={`color(from ${fill} xyz-d65 calc(0.9505 * clamp(0, (0.179 - y) * 1e6, 1)) clamp(0, (0.179 - y) * 1e6, 1) calc(1.089 * clamp(0, (0.179 - y) * 1e6, 1)))`}
                      aria-pressed={isPicked}
                      aria-label={m.gains_seat({
                        to: row.label,
                        from: column.label,
                      })}
                      onclick={() => {
                        picked = isPicked
                          ? null
                          : { to: row.key, from: column.key };
                        isAll = false;
                      }}>{count(cell)}</button
                    >
                  {/if}
                </td>
              {/each}
              <td
                class="py-1 ps-3 text-end font-figures text-[0.8125rem] font-semibold"
                >{count(row.seats)}</td
              >
            </tr>
          {/each}
        </tbody>
        <tfoot>
          <tr class="border-bs-2 border-foreground">
            <th
              scope="row"
              class="sticky inset-s-0 bg-card py-1.5 pe-3 text-start font-semibold"
            >
              {m.gains_losses()}
            </th>
            {#each sheet.columns as column (column.key)}
              <td class="px-0.5 py-1.5">
                <span
                  class="grid place-items-end px-2 font-figures text-[0.8125rem] font-semibold inline-12"
                  >{count(column.seats)}</span
                >
              </td>
            {/each}
            <td
              class="py-1.5 ps-3 text-end font-figures text-[0.8125rem] font-semibold"
              >{count(sheet.changed)}</td
            >
          </tr>
        </tfoot>
      </table>
    </div>
    <div class="grid gap-1.5">
      {#if picked != null}
        <button
          type="button"
          class="justify-self-start rounded-xs border px-2 text-[0.8125rem] block-7 hover:bg-secondary"
          onclick={() => (picked = null)}>{m.gains_clear()}</button
        >
      {/if}
      <ul class="grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3">
        {#each isAll || listed.length <= 24 ? listed : listed.slice(0, 18) as seat, index (`${seat.path ?? seat.name}|${index}`)}
          <li
            class="flex items-baseline gap-2 border-be border-border/60 py-1 text-[0.8125rem]"
          >
            <span
              class="inline-flex shrink-0 translate-y-px gap-px"
              title={seat.isEstimated ? m.gains_estimate_mark() : undefined}
            >
              <span
                class="rounded-xs block-2.5 inline-2.5"
                style:background={seat.toColor}
              ></span>
              <span
                class="rounded-xs opacity-60 block-2.5 inline-2.5"
                style:background={seat.fromColor}
              ></span>
            </span>
            <span class="min-inline-0">
              {#if seat.path != null}
                <a
                  class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: seat.path })}
                  >{seat.name}</a
                >
              {:else}
                <span class="font-semibold">{seat.name}</span>
              {/if}
              <span class="text-muted-foreground"
                >{m.gains_seat({
                  to: seat.toLabel,
                  from: seat.fromLabel,
                })}{seat.isEstimated ? "*" : ""}</span
              >
            </span>
          </li>
        {/each}
      </ul>
      {#if !isAll && listed.length > 24}
        <button
          type="button"
          class="justify-self-start rounded-xs border px-2 text-[0.8125rem] block-7 hover:bg-secondary"
          onclick={() => (isAll = true)}
          >{m.gains_all({ count: count(listed.length) })}</button
        >
      {/if}
      {#if sheet.isNotional === true}
        <p class="text-xs text-muted-foreground max-inline-[72ch]">
          {m.gains_notional({ year: String(sheet.since) })}
        </p>
      {/if}
      {#if sheet.estimated > 0}
        <p class="text-xs text-muted-foreground max-inline-[72ch]">
          * {m.gains_estimated({
            count: count(sheet.estimated),
            year: String(sheet.since),
          })}
        </p>
      {/if}
    </div>
  </section>
{/if}
