<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    lists,
  }: { lists: NonNullable<Extract<PageData, { kind: "contest" }>["lists"]> } =
    $props();

  const REASONS: Record<string, () => string> = {
    resigned: m.lists_reason_resigned,
    died: m.lists_reason_died,
    removed: m.lists_reason_removed,
    expelled: m.lists_reason_expelled,
    disqualified: m.lists_reason_disqualified,
    lost_seat: m.lists_reason_lost_seat,
  };

  const uid = $props.id();

  let isAll = $state(false);
  let isFull = $state(false);

  const winners = $derived(lists.lists.filter((list) => list.seats > 0));
  const shown = $derived(isAll || winners.length === 0 ? lists.lists : winners);
  const ladders = $derived(
    shown.map((list) => {
      let cut = 0;
      for (const [index, member] of list.members.entries()) {
        if (member.fate === "elected") {
          cut = index + 1;
        }
      }
      const next =
        list.seats > 0
          ? (list.members.find(
              (member, index) =>
                index >= cut && member.fate == null && member.elsewhere == null,
            )?.person ?? null)
          : null;
      const changes = lists.replacements.filter(
        (change) => change.list === list.key,
      );
      return {
        ...list,
        cut,
        depth: Math.max(3, cut + 2),
        next,
        changes,
        left: new Set(changes.map((change) => change.replaced.person)),
        joined: new Set(changes.map((change) => change.person)),
      };
    }),
  );
  const isTruncated = $derived(
    ladders.some((ladder) => ladder.members.length > ladder.depth + 1),
  );
</script>

<section
  class="col-span-12 grid content-start gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-labelledby="{uid}-title"
>
  <header class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2.5">
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">
        {m.lists_title()}
      </h2>
      <p class="text-sm text-muted-foreground">
        {m.lists_summary({ seats: lists.seats })}
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      {#if winners.length > 0 && winners.length < lists.lists.length}
        <div class="flex" role="group" aria-label={m.lists_title()}>
          {#each [false, true] as value (value)}
            <button
              type="button"
              class={cn(
                "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    isAll === value,
                },
              )}
              aria-pressed={isAll === value}
              onclick={() => (isAll = value)}
              >{value
                ? m.lists_all({ count: lists.lists.length })
                : m.lists_winners()}</button
            >
          {/each}
        </div>
      {/if}
      {#if isTruncated || isFull}
        <button
          type="button"
          class="flex items-center rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
          aria-expanded={isFull}
          onclick={() => (isFull = !isFull)}
          >{isFull ? m.lists_collapse() : m.lists_expand()}</button
        >
      {/if}
    </div>
  </header>

  <ul
    class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.8125rem] text-muted-foreground"
  >
    <li class="flex items-center gap-1.5">
      <span
        class="rounded-xs bg-[color-mix(in_oklab,var(--foreground)_14%,var(--card))] shadow-[inset_3px_0_0_var(--foreground)] block-3 inline-5"
        aria-hidden="true"
      ></span>
      {m.lists_elected()}
    </li>
    <li class="flex items-center gap-1.5">
      <span class="bg-foreground block-0.5 inline-5" aria-hidden="true"></span>
      <Term label={m.lists_cut()} definition={m.term_cut()} />
    </li>
    <li class="flex items-center gap-1.5">
      <span class="highlighter px-1 text-foreground" aria-hidden="true">Aa</span
      >
      <Term label={m.lists_next()} definition={m.term_next()} />
    </li>
  </ul>

  <div
    class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))] gap-px overflow-hidden rounded-md border"
  >
    {#each ladders as ladder (ladder.key)}
      {const color = $derived(swatch(ladder.color, ladder.key))}
      {const rows = $derived(
        isFull || ladder.members.length <= ladder.depth + 1
          ? ladder.members
          : ladder.members.slice(0, ladder.depth),
      )}
      <article
        class="grid content-start bg-card shadow-[0_0_0_1px_var(--border)]"
        style:--party={color}
        aria-label={ladder.label}
      >
        <header
          class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-bs-[3px] border-(--party) px-2.5 pbs-2 pbe-1.5"
        >
          <Name
            class="truncate text-[0.9375rem] font-semibold min-inline-0"
            label={ladder.short ?? ladder.label}
            {color}
            party={ladder.party}
          />
          <span
            class="font-figures text-xl/none tracking-[-0.02em] tabular-nums"
            aria-label={m.lists_seats({ count: ladder.seats })}
            >{ladder.seats}</span
          >
          <span
            class="truncate text-[0.8125rem] text-muted-foreground"
            title={ladder.label}
            >{ladder.short == null || ladder.short === ladder.label
              ? ""
              : ladder.label}</span
          >
          {#if ladder.votes != null}
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              aria-label={m.lists_votes({ votes: count(ladder.votes) })}
              >{count(ladder.votes)}</span
            >
          {/if}
        </header>
        <ol class="grid border-bs text-sm">
          {#each rows as member, index (member.person)}
            {const isStruck = $derived(
              member.fate === "withdrew" || member.fate === "disqualified",
            )}
            <li
              class={cn(
                "grid grid-cols-[2.25ch_minmax(0,1fr)_auto] items-center gap-x-2 px-2.5 block-7",
                {
                  "bg-[color-mix(in_oklab,var(--party)_16%,var(--card))] shadow-[inset_3px_0_0_var(--party)]":
                    member.fate === "elected",
                },
                {
                  "border-bs-2 border-foreground":
                    index === 0 && ladder.cut === 0 && ladder.seats === 0,
                  "border-be-2 border-foreground":
                    ladder.cut > 0 && index === ladder.cut - 1,
                },
              )}
            >
              <span
                class="text-end font-figures text-xs text-muted-foreground tabular-nums"
                aria-label={m.lists_rank({ position: member.position })}
                >{member.position}</span
              >
              <span class="flex min-inline-0">
                <Name
                  class={cn("truncate min-inline-0", {
                    "font-semibold": member.fate === "elected",
                    "text-muted-foreground line-through decoration-1": isStruck,
                    "highlighter px-0.5": member.person === ladder.next,
                  })}
                  label={member.name}
                  person={member.person}
                />
                {#if member.fate === "elected"}
                  <span class="sr-only">{m.lists_elected()}</span>
                {:else if member.person === ladder.next}
                  <span class="sr-only">{m.lists_next()}</span>
                {/if}
              </span>
              {#if member.fate === "withdrew"}
                <Term
                  class="text-xs text-muted-foreground"
                  label={m.lists_withdrew()}
                  definition={member.note ?? m.term_withdrew_fallback()}
                />
              {:else if member.fate === "disqualified"}
                <Term
                  class="text-xs text-muted-foreground"
                  label={m.lists_disqualified()}
                  definition={member.note ?? m.lists_disqualified()}
                />
              {:else if member.elsewhere != null}
                <Term
                  class="text-xs text-muted-foreground"
                  label={m.lists_elsewhere()}
                  definition={m.lists_elsewhere_detail({
                    contest: member.elsewhere.name,
                  })}
                />
              {:else if ladder.left.has(member.person)}
                <span class="text-xs text-muted-foreground"
                  >{m.lists_replaced()}</span
                >
              {:else if ladder.joined.has(member.person)}
                <span class="text-xs text-muted-foreground"
                  >{m.lists_seated()}</span
                >
              {/if}
            </li>
          {/each}
          {#if rows.length < ladder.members.length}
            <li class="grid">
              <button
                type="button"
                class="px-2.5 text-start text-[0.8125rem] text-muted-foreground block-7 hover:bg-secondary hover:text-foreground"
                onclick={() => (isFull = true)}
                >{m.lists_more({
                  count: ladder.members.length - rows.length,
                })}</button
              >
            </li>
          {/if}
        </ol>
        {#if ladder.changes.length > 0}
          <div class="grid gap-1 border-bs px-2.5 pbs-2 pbe-2.5">
            <h3 class="text-xs font-bold">{m.lists_changes()}</h3>
            <ul class="grid gap-1 text-[0.8125rem]/snug text-muted-foreground">
              {#each ladder.changes as change, index (index)}
                {const reason = $derived(
                  REASONS[change.replaced.via ?? ""]?.(),
                )}
                <li>
                  {reason == null
                    ? m.lists_change({
                        successor: change.name,
                        predecessor: change.replaced.name,
                        date: date(change.date),
                      })
                    : m.lists_change_reason({
                        successor: change.name,
                        predecessor: change.replaced.name,
                        reason,
                        date: date(change.date),
                      })}
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </article>
    {/each}
  </div>

  {#if lists.independents.length > 0}
    <div class="grid gap-1.5">
      <h3 class="text-[0.8125rem] font-bold">{m.lists_independents()}</h3>
      <ul class="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {#each lists.independents as independent (independent.person)}
          <li
            class={cn({
              "rounded-xs bg-[color-mix(in_oklab,var(--foreground)_10%,var(--card))] px-1.5 font-semibold shadow-[inset_3px_0_0_var(--foreground)]":
                independent.isElected,
            })}
          >
            <Name label={independent.name} person={independent.person} />
            {#if independent.isElected}
              <span class="sr-only">{m.lists_elected()}</span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</section>
