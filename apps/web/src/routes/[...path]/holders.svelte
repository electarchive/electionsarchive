<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteSet } from "svelte/reactivity";
  import Name from "#lib/components/name.svelte";
  import { count, credit, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id }: { id: string } = $props();

  const uid = $props.id();

  const holders = createQuery(() =>
    orpc.polities.holders.queryOptions({ input: { id } }),
  );

  const opened = new SvelteSet<string>();

  const parties = $derived(
    new Map((holders.data?.parties ?? []).map((party) => [party.id, party])),
  );
  const groups = $derived(holders.data?.groups ?? []);
  const blocks = $derived.by(() => {
    const result: Array<
      { cards: typeof groups } | { list: (typeof groups)[number] }
    > = [];
    for (const group of groups.filter((entry) => !entry.isFormer)) {
      const last = result.at(-1);
      if (group.isBody || group.rows.length > 1) {
        result.push({ list: group });
      } else if (last != null && "cards" in last) {
        last.cards.push(group);
      } else {
        result.push({ cards: [group] });
      }
    }
    return result;
  });
  const formers = $derived(groups.filter((group) => group.isFormer));
  const VIA: Record<string, (input: { date: string }) => string> = {
    elected: m.holders_elected,
    acting: m.holders_acting,
    appointed: m.holders_appointed,
    trustee: m.holders_trustee,
    list_replacement: m.holders_list_replacement,
    vacant: m.holders_vacant_since,
  };
</script>

{#snippet holder(
  row: (typeof groups)[number]["rows"][number],
  isFormer: boolean,
)}
  {const party = $derived(row.party == null ? null : parties.get(row.party))}
  <span class="grid gap-0.5 min-inline-0">
    {#if row.via === "unknown"}
      <span class="text-muted-foreground">{m.holders_unknown()}</span>
    {:else if row.via === "vacant" || row.person == null}
      <span class="font-semibold">{m.holders_vacant()}</span>
    {:else}
      <Name
        class="justify-self-start font-semibold"
        label={row.name ?? ""}
        color={party == null ? null : swatch(party.color, `party:${party.id}`)}
        person={row.person}
      />
    {/if}
    <span
      class="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground"
    >
      {#if row.since != null}
        {const text = $derived(
          row.via === "unknown"
            ? m.holders_unknown_note({ date: date(row.since) })
            : (VIA[row.via] ?? m.holders_since)({ date: date(row.since) }),
        )}
        {#if row.contest == null}
          <span>{text}</span>
        {:else}
          <a
            class="underline decoration-border underline-offset-2 hover:decoration-foreground"
            href={resolve("/[...path]", { path: row.contest })}>{text}</a
          >
        {/if}
      {/if}
      {#if row.isStale && !isFormer}
        <span class="highlighter px-1 font-semibold text-foreground"
          >{m.holders_stale()}</span
        >
      {/if}
    </span>
    {#if row.elected != null}
      <span
        class="flex flex-wrap items-baseline gap-x-1 text-xs text-muted-foreground"
      >
        {m.holders_won({ date: date(row.elected.date) })}
        <Name label={row.elected.name ?? ""} person={row.elected.person} />
      </span>
    {/if}
    {#if row.next != null}
      <span
        class="flex flex-wrap items-baseline gap-x-1 text-xs text-muted-foreground"
      >
        {#if row.next.person != null && row.next.person === row.person}
          {m.holders_reelected({ date: date(row.next.date) })}
        {:else}
          {m.holders_successor({ date: date(row.next.date) })}
          <Name label={row.next.name ?? ""} person={row.next.person} />
        {/if}
      </span>
    {/if}
    {#if row.due != null}
      <span class="text-xs text-muted-foreground"
        >{m.holders_due({ year: String(row.due) })}</span
      >
    {/if}
  </span>
{/snippet}

{#snippet list(group: (typeof groups)[number])}
  {const isOpen = $derived(opened.has(group.id))}
  {const isLabeled = $derived(group.rows.some((entry) => entry.label != null))}
  <div class="grid content-start gap-1.5">
    <div class="grid gap-0.5">
      <h3 class="text-sm font-semibold">{group.name}</h3>
      {#if group.seats != null}
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.holders_partial({
            count: count(group.rows.length),
            total: count(group.seats),
          })}
        </p>
      {/if}
    </div>
    <ol
      class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,19rem),1fr))] gap-x-6 text-sm"
    >
      {#each isOpen || group.rows.length <= 14 ? group.rows : group.rows.slice(0, 12) as row, at (at)}
        <li
          class={cn(
            "grid items-baseline gap-x-3 border-be border-border/60 py-1.5 pointer-coarse:py-2",
            { "grid-cols-[minmax(4.5rem,auto)_minmax(0,1fr)]": isLabeled },
          )}
        >
          {#if isLabeled}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{row.label ?? m.holders_at_large()}</span
            >
          {/if}
          {@render holder(row, group.isFormer)}
        </li>
      {/each}
    </ol>
    {#if group.rows.length > 14}
      <button
        type="button"
        class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
        aria-expanded={isOpen}
        onclick={() => {
          if (isOpen) {
            opened.delete(group.id);
          } else {
            opened.add(group.id);
          }
        }}
        >{isOpen
          ? m.regions_fewer()
          : m.regions_more({ count: count(group.rows.length) })}</button
      >
    {/if}
  </div>
{/snippet}

{#if groups.length > 0}
  <section
    class="col-span-12 grid content-start gap-4 bg-card px-3.5 pbs-3 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
        {m.holders_title()}
      </h2>
      <p
        class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
      >
        {m.holders_lede()}
      </p>
    </div>
    {#each blocks as block, index (index)}
      {#if "cards" in block}
        <dl
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))] gap-x-6 gap-y-3"
        >
          {#each block.cards as group (group.id)}
            {const row = $derived(group.rows[0])}
            {#if row != null}
              <div
                class={cn(
                  "grid content-start gap-x-3 gap-y-1 border-bs border-border pbs-2 text-sm",
                  { "grid-cols-[auto_minmax(0,1fr)]": row.photo != null },
                )}
              >
                {#if row.photo != null}
                  <dd class="row-span-3 grid content-start gap-1">
                    <img
                      class="aspect-4/5 rounded-sm bg-muted object-cover object-top ring-1 ring-border inline-12"
                      src={row.photo.src}
                      alt={m.photo_alt({ name: row.name ?? "" })}
                      width="48"
                      height="60"
                      loading="lazy"
                    />
                  </dd>
                {/if}
                <dt class="text-xs font-semibold text-muted-foreground">
                  {group.name}{row.label == null ? "" : `, ${row.label}`}
                </dt>
                <dd class="text-[0.9375rem]">{@render holder(row, false)}</dd>
                {#if row.photo != null}
                  <dd>
                    <a
                      class="text-[0.6875rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
                      href={row.photo.source}
                      rel="external noopener">{credit(row.photo)}</a
                    >
                  </dd>
                {/if}
              </div>
            {/if}
          {/each}
        </dl>
      {:else}
        {@render list(block.list)}
      {/if}
    {/each}
    {#if formers.length > 0}
      <details class="grid content-start gap-3">
        <summary
          class="cursor-pointer justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
        >
          {m.holders_former()}
          <span class="font-normal text-muted-foreground"
            >({count(formers.length)})</span
          >
        </summary>
        <div class="grid content-start gap-4 pbs-3">
          {#each formers as group (group.id)}
            {@render list(group)}
          {/each}
        </div>
      </details>
    {/if}
  </section>
{/if}
