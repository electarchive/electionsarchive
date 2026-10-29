<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Name from "#lib/components/name.svelte";
  import { count, date } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    mode,
    members,
    parties,
    start,
    end,
    present = null,
    title = "",
  }: {
    mode: "all" | "changes";
    members: Array<{
      person: string;
      name: string;
      party: string | null;
      segments: Array<{ party: string | null; from: string; to: string }>;
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
    present?: Set<unknown> | null;
    title?: string;
  } = $props();

  const uid = $props.id();
  const VIA: Record<string, () => string> = {
    appointed: m.chamber_via_appointed,
    elected: m.chamber_via_elected,
    by_election: m.chamber_via_by_election,
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
    elevated: m.chamber_end_elevated,
    appointed: m.chamber_end_appointed,
    withdrew: m.chamber_end_withdrew,
    term_end: m.chamber_end_term_end,
  };

  let party = $state<string | null>(page.url.searchParams.get("party"));
  let query = $state(page.url.searchParams.get("member") ?? "");

  $effect(() => {
    keep({ party, member: query.trim() === "" ? null : query });
  });
  let isOpen = $state(false);

  const today = new Date().toISOString().slice(0, 10);
  const groups = $derived([
    ...Map.groupBy(parties, (entry) =>
      entry.label.trim().toLocaleLowerCase(),
    ).values(),
  ]);
  const info = $derived(
    new Map(
      groups.flatMap((group) =>
        group.map((entry) => [
          entry.id,
          new Set(group.map((other) => other.short)).size > 1
            ? { ...entry, short: null }
            : entry,
        ]),
      ),
    ),
  );
  const kin = $derived(
    new Map(
      groups.flatMap((group) =>
        group.map((entry) => [entry.id, group[0]?.id ?? entry.id]),
      ),
    ),
  );
  const fold = (id: string | null) => (id == null ? null : (kin.get(id) ?? id));
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
  const short = new Intl.DateTimeFormat(getLocale(), {
    dateStyle: "medium",
    timeZone: "UTC",
  });
  const years = $derived.by(() => {
    const bands: Array<{ year: number; from: number; to: number }> = [];
    for (
      let year = new Date(low).getUTCFullYear();
      Date.UTC(year, 0, 1) < high;
      year += 1
    ) {
      bands.push({
        year,
        from: Math.max(0, (Date.UTC(year, 0, 1) - low) / (high - low)),
        to: Math.min(1, (Date.UTC(year + 1, 0, 1) - low) / (high - low)),
      });
    }
    return bands;
  });
  const isLate = (member: (typeof members)[number]) =>
    start != null && member.from != null && member.from > start;
  const isEarly = (member: (typeof members)[number]) =>
    member.to != null &&
    member.to < (end ?? today) &&
    member.ended !== "term_end";

  const ordered = $derived(
    [...new Set(members.map((member) => fold(member.party)))].toSorted(
      (a, b) =>
        Number(a == null) - Number(b == null) ||
        (info.get(a ?? "")?.lean ?? Infinity) -
          (info.get(b ?? "")?.lean ?? Infinity) ||
        members.filter((member) => fold(member.party) === b).length -
          members.filter((member) => fold(member.party) === a).length,
    ),
  );
  const shared = $derived.by(() => {
    const lead = members
      .find((member) => member.place != null)
      ?.place?.split(", ", 1)[0];
    return lead != null &&
      members.length > 1 &&
      members.every(
        (member) =>
          member.place == null || member.place.startsWith(`${lead}, `),
      )
      ? lead.length + 2
      : 0;
  });
  const shown = $derived.by(() => {
    const needle = query.trim().toLocaleLowerCase();
    return members
      .filter(
        (member) =>
          (party == null || (fold(member.party) ?? "none") === party) &&
          (needle === "" ||
            member.name.toLocaleLowerCase().includes(needle) ||
            (member.place?.toLocaleLowerCase().includes(needle) ?? false)),
      )
      .toSorted(
        (a, b) =>
          ordered.indexOf(fold(a.party)) - ordered.indexOf(fold(b.party)) ||
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
              <Name
                class="truncate font-semibold"
                label={event.member.name}
                person={event.member.person}
              />
              <span class="truncate text-muted-foreground"
                >{event.member.place?.slice(shared) ??
                  event.member.seat ??
                  ""}</span
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
          <Name
            class="font-semibold"
            label={label(entry)}
            party={entry}
            color={tint(entry)}
            hasSwatch={false}
            hasLink={false}
          />
          <span class="font-figures text-xs text-muted-foreground tabular-nums"
            >{members.filter(
              (member) =>
                fold(member.party) === entry && (present?.has(member) ?? true),
            ).length}</span
          >
        </button>
      {/each}
    </div>
    <div
      class="grid overflow-hidden rounded-sm text-[0.8125rem]/snug ring-1 ring-border"
    >
      <div
        class="grid grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,1.4fr)] items-end gap-x-4 border-be border-border bg-secondary/60 px-2.5 pbs-1.5 pbe-1 text-xs text-muted-foreground max-sm:hidden"
        aria-hidden="true"
      >
        <span>{m.chamber_column_member()}</span>
        <span>{m.chamber_column_seat()}</span>
        {#if start != null}
          <span class="flex items-end gap-3">
            <span class="relative flex-1 block-4">
              {#each years as band (band.year)}
                {#if band.from > 0}
                  <span
                    class="absolute -inset-be-1 bg-border block-2 inline-px"
                    style:inset-inline-start="{band.from * 100}%"
                  ></span>
                {/if}
                {#if band.to - band.from >= 0.1}
                  <span
                    class="absolute inset-bs-0 ps-1 font-figures tabular-nums"
                    style:inset-inline-start="{band.from * 100}%"
                    >{band.year}</span
                  >
                {/if}
              {/each}
              {#if end == null}
                <span class="absolute inset-e-0 inset-bs-0 text-foreground"
                  >{m.chamber_today()}</span
                >
              {/if}
            </span>
            <span class="shrink-0 inline-48">{m.chamber_column_change()}</span>
          </span>
        {/if}
      </div>
      <ul class="grid">
        {#each isOpen || query !== "" || party != null ? shown : shown.slice(0, 40) as member, index (index)}
          {const switches = $derived(
            member.segments.slice(1).map((segment, step) => {
              const isLeft =
                segment.party == null || segment.party === "independent";
              return {
                why: (isLeft ? m.chamber_switch_left : m.chamber_switch_joined)(
                  { party: "\u0000" },
                ),
                party: isLeft
                  ? (member.segments[step]?.party ?? null)
                  : segment.party,
                isSwitch: true,
                on: segment.from,
              };
            }),
          )}
          {const change = $derived(
            isEarly(member)
              ? {
                  why: ENDS[member.ended ?? ""]?.() ?? m.chamber_left(),
                  party: null,
                  isSwitch: false,
                  on: member.to,
                }
              : isLate(member)
                ? {
                    why: VIA[member.acquired ?? ""]?.() ?? m.chamber_joined(),
                    party: null,
                    isSwitch: false,
                    on: member.from,
                  }
                : null,
          )}
          <li
            class="grid grid-cols-[minmax(0,1fr)_minmax(0,45%)] items-center gap-x-4 gap-y-0.5 border-be border-border/60 px-2.5 py-1.5 last:border-be-0 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,1.4fr)]"
            style:--party={tint(member.party)}
          >
            <span class="flex items-center gap-1.5 truncate">
              <Name
                class="flex shrink-0"
                label={label(member.party)}
                party={member.party}
                color={tint(member.party)}
              >
                <span class="rounded-xs bg-(--party) chip block-2.5 inline-2.5"
                ></span>
                <span class="sr-only">{label(member.party)}</span>
              </Name>
              <Name
                class="truncate font-semibold"
                label={member.name}
                person={member.person}
              />
            </span>
            <span
              class="flex items-baseline gap-1.5 truncate text-muted-foreground max-sm:justify-end"
            >
              {#if member.path == null}
                <span class="truncate"
                  >{member.place?.slice(shared) ?? member.seat ?? ""}</span
                >
              {:else}
                <a
                  class="truncate hover:text-foreground hover:underline hover:underline-offset-4"
                  href={resolve("/[...path]", { path: member.path })}
                  >{member.place?.slice(shared) ?? member.seat ?? ""}</a
                >
              {/if}
              {#if !member.isVoting}
                <span class="shrink-0 text-xs">{m.chamber_nonvoting()}</span>
              {/if}
            </span>
            {#if start != null}
              <span
                class={cn(
                  "flex items-center gap-3 self-stretch max-sm:col-span-2",
                  { "max-sm:hidden": change == null && switches.length === 0 },
                )}
              >
                <span
                  class="relative -my-1.5 flex-1 self-stretch max-sm:hidden"
                >
                  {#each years as band (band.year)}
                    {#if band.from > 0}
                      <span
                        class="absolute inset-y-0 bg-border/70 inline-px"
                        style:inset-inline-start="{band.from * 100}%"
                      ></span>
                    {/if}
                  {/each}
                  {#each member.segments as segment, step (step)}
                    {const x0 = $derived(at(segment.from, low))}
                    {const x1 = $derived(at(segment.to, high))}
                    <span
                      class={cn(
                        "absolute inset-bs-1/2 -translate-y-1/2 bg-(--segment) block-1.5",
                        {
                          "rounded-s-full": step === 0,
                          "rounded-e-full": step === member.segments.length - 1,
                        },
                      )}
                      style:--segment={tint(segment.party)}
                      style:inset-inline-start="{x0 * 100}%"
                      style:inline-size="{Math.max(x1 - x0, 0.01) * 100}%"
                    ></span>
                  {/each}
                  <span class="sr-only"
                    >{m.chamber_served({
                      from: short.format(
                        new Date(`${member.from ?? start}T00:00:00Z`),
                      ),
                      to:
                        member.to == null
                          ? m.governments_present()
                          : short.format(new Date(`${member.to}T00:00:00Z`)),
                    })}</span
                  >
                </span>
                <span
                  class="grid shrink-0 gap-0.5 text-xs whitespace-nowrap sm:inline-48 max-sm:ps-4"
                >
                  {#each [...switches, ...(change == null ? [] : [change])].toSorted( (a, b) => (a.on ?? "").localeCompare(b.on ?? "") ) as line, step (step)}
                    <span class="flex items-baseline gap-1.5">
                      {#if line.isSwitch}
                        {const parts = $derived(line.why.split("\u0000"))}
                        <span class="font-semibold"
                          >{parts[0]}<Name
                            label={label(line.party)}
                            party={line.party}
                            color={tint(line.party)}
                            hasSwatch={false}
                          />{parts[1]}</span
                        >
                      {:else}
                        <span class="font-semibold">{line.why}</span>
                      {/if}
                      <span
                        class="font-figures text-muted-foreground tabular-nums"
                        >{line.on == null
                          ? ""
                          : short.format(
                              new Date(`${line.on}T00:00:00Z`),
                            )}</span
                      >
                    </span>
                  {/each}
                </span>
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
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
