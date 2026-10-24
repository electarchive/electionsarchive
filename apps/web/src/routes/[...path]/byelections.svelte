<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteMap } from "svelte/reactivity";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id, within }: { id: string; within?: string } = $props();

  const uid = $props.id();

  const byes = createQuery(() =>
    orpc.seats.byElections.queryOptions({ input: { id, within } }),
  );

  let isAll = $state(false);

  const sheet = $derived.by(() => {
    const data = byes.data;
    if (data == null) {
      return null;
    }
    const fold = (key: string | null) =>
      key == null ? null : key.startsWith("person:") ? "independent" : key;
    const info = new Map(data.parties.map((party) => [party.key, party]));
    const label = (key: string) =>
      key === "independent"
        ? m.seats_independents()
        : (info.get(key)?.short ?? info.get(key)?.label ?? "");
    const color = (key: string | null) =>
      key == null || key === "independent"
        ? swatch(null, "")
        : swatch(info.get(key)?.color ?? null, `party:${key}`);
    const net = new SvelteMap<string, number>();
    const reasons: Record<string, (input: { name: string }) => string> = {
      died: m.byes_reason_died,
      resigned: m.byes_reason_resigned,
      elevated: m.byes_reason_elevated,
      appointed: m.byes_reason_appointed,
      expelled: m.byes_reason_expelled,
      disqualified: m.byes_reason_disqualified,
      annulled: m.byes_reason_annulled,
      removed: m.byes_reason_removed,
      withdrew: m.byes_reason_withdrew,
    };
    const rows = data.rows.map((row) => {
      const to = fold(row.to);
      const from = fold(row.from);
      const isGain = to != null && from != null && to !== from;
      if (isGain) {
        net.set(to, (net.get(to) ?? 0) + 1);
        net.set(from, (net.get(from) ?? 0) - 1);
      }
      return {
        ...row,
        isGain,
        toColor: color(to),
        fromColor: color(from),
        result:
          to == null
            ? null
            : isGain
              ? m.gains_seat({ to: label(to), from: label(from) })
              : m.byes_hold({ party: label(to) }),
        why:
          row.member == null
            ? null
            : (reasons[row.reason ?? ""] ?? m.byes_reason_none)({
                name: row.member.name,
              }),
      };
    });
    return {
      until: data.until,
      general: data.general,
      rows,
      changed: rows.filter((row) => row.isGain).length,
      net: [...net.entries()]
        .filter(([, seats]) => seats !== 0)
        .toSorted((a, b) => b[1] - a[1])
        .map(([key, seats]) => ({
          key,
          seats,
          label: label(key),
          color: color(key),
        })),
    };
  });
</script>

{#if sheet != null && sheet.rows.length > 0}
  <section
    class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">{m.byes_title()}</h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {#if sheet.general != null}
          {#if sheet.general.path == null}
            {m.byes_parliament({ date: date(sheet.general.date) })}
          {:else}
            <a
              class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={resolve("/[...path]", { path: sheet.general.path })}
              >{m.byes_parliament({ date: date(sheet.general.date) })}</a
            >
          {/if}
        {/if}
        {sheet.until == null
          ? m.byes_lede_open({ count: sheet.rows.length })
          : m.byes_lede({
              count: sheet.rows.length,
              year: String(sheet.until),
            })}
        {sheet.changed === 0
          ? m.byes_changed_none()
          : m.byes_changed({ count: sheet.changed })}
      </p>
    </div>
    {#if sheet.net.length > 0}
      <dl
        class="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[0.8125rem]"
      >
        <dt class="text-xs font-semibold text-muted-foreground">
          {m.byes_net()}
        </dt>
        {#each sheet.net as party (party.key)}
          <dd class="inline-flex items-center gap-1.5">
            <span
              class="shrink-0 rounded-xs block-2 inline-2"
              style:background={party.color}
            ></span>
            {party.label}
            <span class="font-figures font-semibold"
              >{party.seats > 0 ? "+" : "−"}{count(Math.abs(party.seats))}</span
            >
          </dd>
        {/each}
      </dl>
    {/if}
    <ol class="grid gap-x-6 md:grid-cols-2">
      {#each isAll || sheet.rows.length <= 16 ? sheet.rows : sheet.rows.filter((row, index) => index < 12 || row.isHere) as row (row.path ?? `${row.date}${row.name}`)}
        <li
          class={cn(
            "grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2 border-be border-border/60 py-1.5 text-[0.8125rem]",
            { "-mx-1.5 bg-secondary px-1.5": row.isHere },
          )}
          aria-current={row.isHere ? "page" : undefined}
        >
          <span class="inline-flex translate-y-px gap-px">
            <span
              class="rounded-xs block-2.5 inline-2.5"
              style:background={row.toColor}
            ></span>
            <span
              class="rounded-xs opacity-60 block-2.5 inline-2.5"
              style:background={row.fromColor}
            ></span>
          </span>
          <span>
            {#if row.isHere}
              <span class="text-xs font-semibold">{m.byes_here()}:</span>
            {/if}
            {#if row.path != null}
              <a
                class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                href={resolve("/[...path]", { path: row.path })}>{row.name}</a
              >
            {:else}
              <span class="font-semibold">{row.name}</span>
            {/if}
            <span class="text-muted-foreground"
              >{date(row.date)}.{#if row.result != null}{" "}<span
                  class={cn({ "font-medium text-foreground": row.isGain })}
                  >{row.result}.</span
                >{/if}</span
            >
          </span>
          {#if row.majority != null || row.why != null}
            <span class="col-start-2 text-xs text-pretty text-muted-foreground">
              {#if row.majority != null}{m.byes_majority({
                  count: count(row.majority),
                })}{/if}
              {#if row.why != null}{row.why}{/if}
            </span>
          {/if}
        </li>
      {/each}
    </ol>
    {#if !isAll && sheet.rows.length > 16}
      <button
        type="button"
        class="justify-self-start rounded-xs border px-2 text-[0.8125rem] block-7 hover:bg-secondary"
        onclick={() => (isAll = true)}
        >{m.byes_all({ count: count(sheet.rows.length) })}</button
      >
    {/if}
  </section>
{/if}
