<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { count, date } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    mode,
    members,
    parties,
    start,
    end,
    title = "",
  }: {
    mode: "all" | "changes";
    members: Array<{
      person: string;
      name: string;
      party: string | null;
      left: string | null;
      place: string | null;
      path: string | null;
      seat: string | null;
      from: string | null;
      to: string | null;
      acquired: string | null;
      ended: string | null;
      isVoting: boolean;
    }>;
    parties: Array<{
      id: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
    }>;
    start: string | null;
    end: string | null;
    title?: string;
  } = $props();

  const uid = $props.id();
  const VIA: Record<string, () => string> = {
    appointed: m.chamber_via_appointed,
    elected: m.chamber_via_elected,
    succession: m.chamber_via_succession,
    list_replacement: m.chamber_via_list_replacement,
    co_opted: m.chamber_via_co_opted,
    trustee: m.chamber_via_trustee,
    acting: m.chamber_via_acting,
  };
  const ENDS: Record<string, () => string> = {
    resigned: m.chamber_end_resigned,
    died: m.chamber_end_died,
    removed: m.chamber_end_removed,
    annulled: m.chamber_end_annulled,
    dissolved: m.chamber_end_dissolved,
    expelled: m.chamber_end_expelled,
    lost_seat: m.chamber_end_lost_seat,
    disqualified: m.chamber_end_disqualified,
    term_end: m.chamber_end_term_end,
  };

  let party = $state<string | null>(page.url.searchParams.get("party"));
  let query = $state(page.url.searchParams.get("member") ?? "");

  $effect(() => {
    keep({ party, member: query.trim() === "" ? null : query });
  });
  let isOpen = $state(false);

  const today = new Date().toISOString().slice(0, 10);
  const info = $derived(new Map(parties.map((entry) => [entry.id, entry])));
  const tint = (id: string | null) =>
    swatch(info.get(id ?? "")?.color ?? null, `party:${id ?? ""}`);
  const label = (id: string | null) =>
    id == null
      ? m.record_none()
      : (info.get(id)?.short ?? info.get(id)?.label ?? "");
  const low = $derived(Date.parse(start ?? today));
  const high = $derived(Math.max(Date.parse(end ?? today), low + 1));
  const at = (value: string | null, fallback: number) =>
    Math.min(
      1,
      Math.max(
        0,
        ((value == null ? fallback : Date.parse(value)) - low) / (high - low),
      ),
    );
  const isLate = (member: (typeof members)[number]) =>
    start != null && member.from != null && member.from > start;
  const isEarly = (member: (typeof members)[number]) =>
    member.to != null &&
    member.to < (end ?? today) &&
    member.ended !== "term_end";

  const ordered = $derived(
    [...new Set(members.map((member) => member.party))].toSorted(
      (a, b) =>
        Number(a == null) - Number(b == null) ||
        (info.get(a ?? "")?.lean ?? Infinity) -
          (info.get(b ?? "")?.lean ?? Infinity) ||
        members.filter((member) => member.party === b).length -
          members.filter((member) => member.party === a).length,
    ),
  );
  const shown = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    return members
      .filter(
        (member) =>
          (party == null || (member.party ?? "none") === party) &&
          (needle === "" ||
            member.name.toLocaleLowerCase().includes(needle) ||
            (member.place?.toLocaleLowerCase().includes(needle) ?? false)),
      )
      .toSorted(
        (a, b) =>
          ordered.indexOf(a.party) - ordered.indexOf(b.party) ||
          Number(a.place == null) - Number(b.place == null) ||
          (a.place ?? "").localeCompare(b.place ?? "", undefined, {
            numeric: true,
          }) ||
          (a.from ?? "").localeCompare(b.from ?? ""),
      );
  });
  const events = $derived(
    members
      .flatMap((member) => [
        ...(isLate(member)
          ? [
              {
                member,
                on: member.from ?? "",
                isJoin: true,
                why: VIA[member.acquired ?? ""]?.() ?? null,
              },
            ]
          : []),
        ...(isEarly(member)
          ? [
              {
                member,
                on: member.to ?? "",
                isJoin: false,
                why: ENDS[member.ended ?? ""]?.() ?? null,
              },
            ]
          : []),
      ])
      .toSorted(
        (a, b) =>
          a.on.localeCompare(b.on) || Number(a.isJoin) - Number(b.isJoin),
      ),
  );
</script>

{#if mode === "changes"}
  <div class="grid gap-2">
    <h2 class="text-[0.8125rem] font-bold">{m.chamber_changes()}</h2>
    {#if events.length === 0}
      <p class="text-sm text-muted-foreground">{m.chamber_changes_none()}</p>
    {:else}
      <ol
        class="grid overflow-y-auto rounded-sm text-[0.8125rem] ring-1 ring-border max-block-88"
      >
        {#each events as event, index (index)}
          <li
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 px-2.5 py-1.5 last:border-be-0 sm:grid-cols-[auto_minmax(0,1fr)_auto]"
          >
            <span
              class="font-figures text-xs whitespace-nowrap text-muted-foreground tabular-nums sm:inline-34 max-sm:col-span-2"
              >{date(event.on)}</span
            >
            <span class="flex items-baseline gap-1.5 truncate">
              <span
                class="shrink-0 self-center rounded-xs block-2 inline-2"
                style:background={tint(event.member.party)}
              ></span>
              <a
                class="truncate font-semibold hover:underline hover:underline-offset-4"
                href={resolve("/id/[id]", { id: event.member.person })}
                >{event.member.name}</a
              >
              <span class="truncate text-muted-foreground"
                >{event.member.place ?? event.member.seat ?? ""}</span
              >
            </span>
            <span
              class={cn("text-xs font-semibold whitespace-nowrap", {
                "text-muted-foreground": !event.isJoin,
              })}
              >{event.why ??
                (event.isJoin ? m.chamber_joined() : m.chamber_left())}</span
            >
          </li>
        {/each}
      </ol>
    {/if}
  </div>
{:else}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="{uid}-title"
  >
    <div class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div class="grid gap-0.5">
        <h2 id="{uid}-title" class="heading">
          {title}
          <span class="font-figures text-base font-normal text-muted-foreground"
            >{count(members.length)}</span
          >
        </h2>
        {#if start != null}
          <p class="text-[0.8125rem] text-muted-foreground">
            {m.chamber_members_lede()}
          </p>
        {/if}
      </div>
      <input
        class="rounded-sm border border-input bg-card px-2.5 text-sm block-8 inline-[min(100%,16rem)] placeholder:text-muted-foreground"
        type="search"
        placeholder={m.members_filter()}
        aria-label={m.members_filter()}
        bind:value={query}
      />
    </div>
    <div
      class="flex flex-wrap gap-1.5"
      role="group"
      aria-label={m.members_party()}
    >
      {#each ordered as entry (entry ?? "none")}
        <button
          type="button"
          class={cn(
            "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
            { "marked bg-secondary": party === (entry ?? "none") },
          )}
          aria-pressed={party === (entry ?? "none")}
          onclick={() =>
            (party = party === (entry ?? "none") ? null : (entry ?? "none"))}
        >
          <span
            class="rounded-xs block-2.5 inline-2.5"
            style:background={tint(entry)}
          ></span>
          <span class="font-semibold">{label(entry)}</span>
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{members.filter((member) => member.party === entry).length}</span
          >
        </button>
      {/each}
    </div>
    <ul
      class="grid overflow-hidden rounded-sm text-[0.8125rem]/snug ring-1 ring-border"
    >
      {#each isOpen || query !== "" || party != null ? shown : shown.slice(0, 40) as member, index (index)}
        {const x0 = $derived(at(member.from, low))}
        {const x1 = $derived(at(member.to, high))}
        <li
          class="grid grid-cols-[minmax(0,1fr)] items-center gap-x-4 gap-y-1 border-be border-border/60 px-2.5 py-1.5 last:border-be-0 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,1.4fr)]"
          style:--party={tint(member.party)}
        >
          <span class="flex items-center gap-1.5 truncate">
            <span class="shrink-0 rounded-xs bg-(--party) block-2.5 inline-2.5"
            ></span>
            <a
              class="truncate font-semibold hover:underline hover:underline-offset-4"
              href={resolve("/id/[id]", { id: member.person })}>{member.name}</a
            >
          </span>
          <span
            class="flex items-baseline gap-1.5 truncate text-muted-foreground"
          >
            {#if member.path == null}
              <span class="truncate">{member.place ?? member.seat ?? ""}</span>
            {:else}
              <a
                class="truncate hover:text-foreground hover:underline hover:underline-offset-4"
                href={resolve("/[...path]", { path: member.path })}
                >{member.place ?? member.seat ?? ""}</a
              >
            {/if}
            {#if !member.isVoting}
              <span class="shrink-0 text-xs">{m.chamber_nonvoting()}</span>
            {/if}
            {#if member.left != null}
              <span class="shrink-0 text-xs font-semibold text-foreground"
                >{m.chamber_switched({ party: label(member.left) })}</span
              >
            {/if}
          </span>
          {#if start != null}
            <span class="flex items-center gap-2">
              <span
                class="relative block flex-1 rounded-full bg-muted block-2"
                aria-hidden="true"
              >
                <span
                  class="absolute inset-y-0 rounded-full bg-(--party)"
                  style:inset-inline-start="{x0 * 100}%"
                  style:inline-size="{Math.max(x1 - x0, 0.01) * 100}%"
                ></span>
              </span>
              <span
                class="shrink-0 text-end text-xs text-muted-foreground inline-24"
                >{#if isEarly(member)}{ENDS[member.ended ?? ""]?.() ??
                    date(member.to)}{:else if isLate(member)}{VIA[
                    member.acquired ?? ""
                  ]?.() ?? date(member.from)}{/if}</span
              >
            </span>
          {/if}
        </li>
      {/each}
    </ul>
    {#if shown.length === 0}
      <p class="text-sm text-muted-foreground">{m.members_none()}</p>
    {:else if !isOpen && query === "" && party == null && shown.length > 40}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 hover:bg-secondary"
        onclick={() => (isOpen = true)}
        >{m.members_all({ count: count(shown.length) })}</button
      >
    {/if}
  </section>
{/if}
