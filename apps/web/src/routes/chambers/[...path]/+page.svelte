<script lang="ts">
  import Seats from "#routes/[...path]/seats.svelte";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery, keepPreviousData } from "@tanstack/svelte-query";
  import { partyOn, segments, stand } from "#lib/caucus.js";
  import Name from "#lib/components/name.svelte";
  import * as Select from "#lib/components/ui/select/index.js";
  import { Slider } from "#lib/components/ui/slider/index.js";
  import { count, date, definite } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Council from "./council.svelte";
  import Roster from "./roster.svelte";
  import Stream from "./stream.svelte";

  const { data }: { data: PageData } = $props();

  const KINDS: Record<string, () => string> = {
    lower_house: m.chamber_kind_lower_house,
    upper_house: m.chamber_kind_upper_house,
    unicameral: m.chamber_kind_unicameral,
  };

  let selected = $derived(Math.max(0, data.terms.length - 1));

  const body = $derived(data.body);
  const council = $derived(data.council);
  const isParty = $derived(council == null || council.isPartisan);
  const term = $derived(data.terms[selected] ?? null);
  const previous = $derived(data.terms[selected - 1] ?? null);
  const latest = $derived(data.terms.at(-1) ?? null);
  const info = $derived(
    new Map(data.parties.map((party) => [party.id, party])),
  );
  const year = (value: string | null) => value?.slice(0, 4) ?? "";
  const span = (from: string, to: string | null) =>
    to != null && year(from) === year(to)
      ? year(from)
      : m.polity_span({
          first: year(from),
          last: to == null ? m.governments_present() : year(to),
        });

  const members = createQuery(() => {
    const at = term;
    const isLatest = selected === data.terms.length - 1;
    return {
      ...orpc.bodies.members.queryOptions({
        input: {
          id: data.id,
          from: at?.start ?? "1000-01-01",
          until: at?.end ?? undefined,
        },
      }),
      enabled: at != null,
      initialData: isLatest ? (data.members ?? undefined) : undefined,
      placeholderData: keepPreviousData,
    };
  });

  let chosen = $state<{ term: string; day: string } | null>(null);
  const first = $derived(members.data?.start ?? term?.start ?? "");
  const on = $derived(
    chosen != null && chosen.term === term?.start ? chosen.day : first,
  );
  const DAY = 864e5;
  const roll = $derived(
    (members.data?.members ?? []).map((member) => ({
      ...member,
      party: partyOn(member, on, first),
      segments: segments(
        member,
        first,
        members.data?.end ?? new Date().toISOString().slice(0, 10),
      ),
    })),
  );
  const crew = $derived(
    stand(
      roll.flatMap((member) => [
        member.party,
        ...member.segments.map((segment) => segment.party),
      ]),
      members.data?.parties ?? [],
    ),
  );
  const range = $derived({
    low: Date.parse(first) / DAY,
    high:
      Date.parse(members.data?.end ?? new Date().toISOString().slice(0, 10)) /
        DAY -
      1,
  });
  const sitting = $derived.by(() => {
    if (members.data == null) {
      return [];
    }
    const present = roll.filter(
      (member) =>
        member.isVoting &&
        (!members.data.isSeated ||
          ((member.from ?? "") <= on && (member.to == null || member.to > on))),
    );
    const held = new Set(present.map((member) => member.seat));
    const late =
      on === first && members.data.isSeated
        ? Map.groupBy(
            roll
              .filter(
                (member) =>
                  member.isVoting &&
                  (member.seat == null
                    ? member.acquired === "elected"
                    : !held.has(member.seat)) &&
                  member.from != null &&
                  member.from > on &&
                  Date.parse(member.from) - Date.parse(on) < 30 * DAY,
              )
              .toSorted((a, b) => (a.from ?? "").localeCompare(b.from ?? "")),
            (member) => member.seat ?? member.person,
          )
            .values()
            .flatMap((group) => group.slice(0, 1))
            .toArray()
        : [];
    present.push(...late);
    return members.data.apportionment.length === 0
      ? present
      : Map.groupBy(present, (member) => member.seat ?? member.person)
          .values()
          .map(
            (group) =>
              group.toSorted((a, b) =>
                (b.from ?? "").localeCompare(a.from ?? ""),
              )[0],
          )
          .filter((member) => member != null)
          .toArray();
  });
  const open = $derived(
    (members.data?.apportionment ?? []).filter(
      (row) => row.from <= on && (row.to == null || row.to > on),
    ),
  );
  const apportioned = $derived.by(() => {
    let total = 0;
    for (const row of open) {
      total += row.seats;
    }
    return total;
  });
  const isCounted = $derived(
    members.data?.isSeated === true && (open.length > 0 || on !== first),
  );
  const own = $derived(new Map(crew.map((party) => [party.id, party])));
  const label = (party: string | null) => {
    const found = info.get(party ?? "") ?? own.get(party ?? "");
    return party == null
      ? m.record_none()
      : (found?.short ?? found?.label ?? "");
  };
  const places = $derived(
    [
      ...new Map(
        sitting.flatMap((member) =>
          member.place == null
            ? []
            : [[member.place, { name: member.place, path: member.path }]],
        ),
      ).values(),
    ].toSorted((a, b) =>
      a.name.localeCompare(b.name, undefined, { numeric: true }),
    ),
  );
  const hemicycle = $derived.by(() => {
    const keys = new Set((term?.parties ?? []).map((entry) => entry.party));
    const home = (party: string | null) =>
      party == null || keys.has(party)
        ? party
        : (own.get(party)?.parent ?? party);
    const seated = Map.groupBy(
      sitting.toSorted(
        (a, b) =>
          (a.place ?? "").localeCompare(b.place ?? "", undefined, {
            numeric: true,
          }) || a.name.localeCompare(b.name),
      ),
      (member) => home(member.party),
    );
    const rows = isCounted
      ? seated
          .entries()
          .map(([party, list]) => ({ party, seats: list.length }))
          .toArray()
      : (term?.parties ?? []);
    return rows.map((entry) => {
      const party =
        entry.party == null
          ? null
          : (info.get(entry.party) ?? own.get(entry.party));
      return {
        key: entry.party ?? "none",
        label: party?.label ?? m.record_none(),
        short: party?.short ?? null,
        color: party?.color ?? null,
        lean: party?.lean ?? null,
        seats: entry.seats,
        here: 0,
        members: (seated.get(entry.party) ?? []).map((member) => ({
          place: places.findIndex((place) => place.name === member.place),
          name: member.name,
          position: null,
          person: member.person,
          party: member.party,
          affiliation:
            member.party == null || member.party === entry.party
              ? null
              : (own.get(member.party)?.label ?? null),
        })),
      };
    });
  });
  const held = $derived.by(() => {
    let total = 0;
    for (const party of hemicycle) {
      total += party.seats;
    }
    return total;
  });
  const seceded = $derived.by(() => {
    const taken = Map.groupBy(sitting, (member) => member.region);
    let total = 0;
    for (const row of open) {
      if (
        row.gaps.some(
          (gap) => gap.from <= on && (gap.to == null || gap.to > on),
        )
      ) {
        total += Math.max(0, row.seats - (taken.get(row.area)?.length ?? 0));
      }
    }
    return total;
  });
  const change = $derived.by(() => {
    if (previous == null) {
      return null;
    }
    const before = new Map(
      previous.parties.map((entry) => [entry.party ?? "none", entry.seats]),
    );
    return Object.fromEntries(
      hemicycle.flatMap((entry) => {
        const was = before.get(entry.key);
        return was == null ? [] : [[entry.key, entry.seats - was]];
      }),
    );
  });
  const lead = (parties: Array<{ party: string | null; seats: number }>) =>
    Map.groupBy(
      parties,
      (row) =>
        (info.get(row.party ?? "") ?? own.get(row.party ?? ""))?.caucus ??
        body.partners.find((pair) => pair.includes(row.party ?? ""))?.[0] ??
        row.party,
    )
      .entries()
      .map(([key, rows]) => {
        let seats = 0;
        for (const row of rows) {
          seats += row.seats;
        }
        const caucus = rows.some(
          (row) =>
            (info.get(row.party ?? "") ?? own.get(row.party ?? ""))?.caucus ===
            key,
        );
        return {
          party: caucus ? key : (rows[0]?.party ?? null),
          id:
            caucus || rows.length === 1
              ? caucus
                ? key
                : (rows[0]?.party ?? null)
              : null,
          name: caucus
            ? label(key)
            : rows.map((row) => label(row.party)).join("/"),
          full:
            !caucus && rows.length === 1 && rows[0]?.party != null
              ? ((info.get(rows[0].party) ?? own.get(rows[0].party))?.label ??
                label(rows[0].party))
              : null,
          seats,
        };
      })
      .toArray()
      .toSorted((a, b) => b.seats - a.seats)[0] ?? null;
  const leader = $derived(
    lead(
      hemicycle.map((entry) => ({
        party: entry.key === "none" ? null : entry.key,
        seats: entry.seats,
      })),
    ),
  );
  const largest = $derived(
    selected === data.terms.length - 1 && members.data != null
      ? leader
      : lead(data.terms.at(-1)?.parties ?? []),
  );
  const whole = $derived(term == null ? 0 : (term.size ?? term.total));
  const cabinets = $derived(
    term == null
      ? []
      : data.governments.filter(
          (cabinet) =>
            (term.end == null || cabinet.from < term.end) &&
            (cabinet.to == null || cabinet.to > term.start),
        ),
  );
</script>

<svelte:head>
  <title>{body.name} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta property="og:title" content={body.name} />
</svelte:head>

<main class="pbe-16">
  <article class="grid grid-cols-12 gap-px overflow-clip border-be bg-border">
    <header class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3.5 pbe-3.5">
      <nav aria-label={m.chamber_kind_other()}>
        <ol
          class="flex flex-wrap items-center gap-x-1.5 text-[0.8125rem] text-muted-foreground"
        >
          {#each body.crumbs as crumb, index (index)}
            <li class="flex items-center gap-1.5">
              {#if index > 0}<span aria-hidden="true">/</span>{/if}
              {#if crumb.path == null}
                <span>{crumb.name}</span>
              {:else}
                <a
                  class="hover:text-foreground hover:underline hover:underline-offset-4"
                  href={resolve("/[...path]", { path: crumb.path })}
                  >{crumb.name}</a
                >
              {/if}
            </li>
          {/each}
        </ol>
      </nav>
      <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div class="grid gap-1">
          <h1 class="title">
            {body.name}
          </h1>
          <p class="text-[0.9375rem] text-muted-foreground">
            {m.chamber_of({
              kind: (KINDS[body.kind] ?? m.chamber_kind_other)(),
              polity: body.polity.name,
            })}
          </p>
        </div>
        {#if body.siblings.length > 1}
          <nav class="flex max-sm:inline-full" aria-label={m.record_tabs()}>
            {#each body.siblings as sibling (sibling.id)}
              <a
                class={cn(
                  "-ms-px flex items-center justify-center border border-input bg-card px-2.5 py-1 text-center text-[0.8125rem]/tight font-semibold keycap min-block-8 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary max-sm:flex-1",
                  {
                    "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                      sibling.id === data.id,
                  },
                )}
                aria-current={sibling.id === data.id ? "page" : undefined}
                href={resolve("/[...path]", { path: sibling.path })}
                >{sibling.name}</a
              >
            {/each}
          </nav>
        {/if}
      </div>
    </header>

    {#if latest != null && isParty}
      <dl
        class="col-span-12 flex flex-wrap items-baseline gap-x-7 gap-y-1.5 bg-card px-3.5 py-3"
      >
        {#if data.seats != null}
          <div class="flex items-baseline gap-2">
            <dt class="text-[0.8125rem] text-muted-foreground">
              {m.chamber_seats()}
            </dt>
            <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
              {count(data.seats)}
            </dd>
          </div>
        {/if}
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.chamber_terms()}
          </dt>
          <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
            {count(data.terms.length)}
            <span class="text-sm text-muted-foreground"
              >{span(data.terms[0]?.start ?? latest.start, body.closed)}</span
            >
          </dd>
        </div>
        <div class="flex items-baseline gap-2">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {m.chamber_largest()}
          </dt>
          <dd class="flex items-baseline gap-2">
            <span
              class="self-center rounded-xs block-3 inline-3"
              style:background={swatch(
                info.get(largest?.party ?? "")?.color ?? null,
                `party:${largest?.party ?? ""}`,
              )}
            ></span>
            <Name
              class="text-xl font-[760] tracking-[-0.015em]"
              label={largest?.name ?? ""}
              party={largest?.id}
              color={swatch(
                info.get(largest?.party ?? "")?.color ?? null,
                `party:${largest?.party ?? ""}`,
              )}
              hasSwatch={false}
            />
            <span
              class="font-figures text-base text-muted-foreground tabular-nums"
              >{count(largest?.seats ?? 0)}</span
            >
          </dd>
        </div>
        {#if body.next != null}
          <div class="flex items-baseline gap-2">
            <dt class="text-[0.8125rem] text-muted-foreground">
              {m.chamber_next()}
            </dt>
            <dd class="flex items-baseline gap-2">
              {#if body.next.path == null}
                <span class="text-xl font-[760] tracking-[-0.015em]"
                  >{date(body.next.date)}</span
                >
              {:else}
                <a
                  class="text-xl font-[760] tracking-[-0.015em] underline decoration-border underline-offset-4 hover:decoration-foreground"
                  href="{resolve('/[...path]', { path: body.next.path })}#{body
                    .next.anchor}">{date(body.next.date)}</a
                >
              {/if}
              <span class="text-base text-muted-foreground"
                >{m.chamber_next_seats({ count: body.next.seats })}</span
              >
            </dd>
          </div>
        {/if}
        {#if body.tenures > 0}
          <div class="flex items-baseline gap-2">
            <dt class="text-[0.8125rem] text-muted-foreground">
              {m.chamber_members_count()}
            </dt>
            <dd class="font-figures text-xl font-[760] tracking-[-0.015em]">
              {count(body.tenures)}
            </dd>
          </div>
        {/if}
      </dl>

      <Stream
        terms={data.terms}
        parties={data.parties}
        governments={data.governments}
        basis={data.basis}
        bind:selected
      />
    {/if}

    {#if term != null && isParty}
      <div
        class="col-span-12 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 bg-card px-3.5 py-2.5"
      >
        <div class="flex items-center gap-1.5">
          <button
            type="button"
            class="grid place-items-center rounded-sm border border-input bg-card keycap block-8 inline-8 hover:bg-secondary disabled:opacity-40"
            aria-label={m.chamber_term_previous()}
            disabled={selected === 0}
            onclick={() => (selected = Math.max(0, selected - 1))}
            ><ChevronLeftIcon class="block-4 inline-4" /></button
          >
          <Select.Root
            type="single"
            value={String(selected)}
            onValueChange={(value: string) => (selected = Number(value))}
          >
            <Select.Trigger
              class="font-figures text-[0.9375rem] font-semibold tabular-nums"
              aria-label={m.chamber_term_pick()}
              >{span(term.start, term.end)}</Select.Trigger
            >
            <Select.Content align="start">
              {#each data.terms as entry, index (entry.start)}
                <Select.Item
                  value={String(index)}
                  label={span(entry.start, entry.end)}
                />
              {/each}
            </Select.Content>
          </Select.Root>
          <button
            type="button"
            class="grid place-items-center rounded-sm border border-input bg-card keycap block-8 inline-8 hover:bg-secondary disabled:opacity-40"
            aria-label={m.chamber_term_next()}
            disabled={selected === data.terms.length - 1}
            onclick={() =>
              (selected = Math.min(data.terms.length - 1, selected + 1))}
            ><ChevronRightIcon class="block-4 inline-4" /></button
          >
        </div>
        <p class="text-[0.8125rem] text-muted-foreground">
          {#if term.election == null}
            {m.chamber_no_election()}
          {:else}
            {m.chamber_elected()}
            {#if term.election.path == null}
              <span class="font-semibold text-foreground"
                >{term.election.name}</span
              >
            {:else}
              <a
                class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
                href={resolve("/[...path]", { path: term.election.path })}
                >{term.election.name}</a
              >
            {/if}
            <span class="font-figures text-xs tabular-nums"
              >{date(term.election.date)}</span
            >
          {/if}
        </p>
      </div>

      {#if members.data?.isSeated === true && range.high > range.low}
        <div
          class="col-span-12 flex flex-wrap items-center gap-x-4 gap-y-2 bg-card px-3.5 py-2.5"
        >
          <span
            class="font-figures text-[0.8125rem] font-semibold tabular-nums min-inline-36"
            >{m.chamber_on({ date: date(on) })}</span
          >
          <Slider
            type="single"
            class="flex-1 max-inline-120 min-inline-40"
            aria-label={m.chamber_on_pick()}
            min={range.low}
            max={range.high}
            step={1}
            value={Date.parse(on) / DAY}
            onValueChange={(value: number) => {
              chosen = {
                term: term.start,
                day: new Date(value * DAY).toISOString().slice(0, 10),
              };
            }}
          />
          <button
            type="button"
            class="rounded-sm border border-input bg-card px-2.5 py-1 text-[0.8125rem] font-semibold keycap hover:bg-secondary disabled:opacity-40"
            disabled={on === first}
            onclick={() => (chosen = null)}>{m.chamber_on_first()}</button
          >
        </div>
      {/if}

      <div class="col-span-12 grid lg:col-span-4">
        {#key term.start}
          <Seats
            isLinked
            parties={hemicycle}
            {places}
            here={null}
            place={body.name}
            link={null}
            size={open.length > 0
              ? apportioned
              : term.size != null && held <= term.size
                ? term.size
                : null}
            vacant={open.length > 0
              ? Math.max(0, apportioned - held - seceded)
              : term.size == null || !term.isSeated
                ? 0
                : Math.max(0, term.size - held)}
            {seceded}
            official={seceded > 0 && term.size != null ? term.size : null}
            isComplete={!term.isPartial}
            {change}
            partners={body.partners}
          />
        {/key}
      </div>

      <section
        class="col-span-12 grid content-start gap-4 bg-card p-3.5 lg:col-span-8"
      >
        {#if leader != null}
          <p
            class="font-serif text-[1.375rem]/snug font-[560] tracking-[-0.005em] text-pretty max-inline-[44ch]"
          >
            {const parts = $derived(
              (term.isPartial
                ? m.chamber_renewal({
                    party: "\u0000",
                    seats: count(leader.seats),
                    total: count(term.total),
                  })
                : (leader.seats >= Math.floor(whole / 2) + 1
                    ? m.chamber_majority
                    : m.chamber_plurality)({
                    party: "\u0000",
                    seats: count(leader.seats),
                    total: count(whole),
                  })
              ).split("\u0000"),
            )}
            {parts[0]}<Name
              label={definite(leader.full ?? leader.name, parts[0] === "")}
              party={leader.id}
              hasSwatch={false}
            />{parts[1]}
          </p>
        {/if}
        {#if cabinets.length > 0}
          <div class="grid gap-2">
            <h2 class="text-[0.8125rem] font-bold">
              {m.chamber_governments()}
            </h2>
            <ol
              class="grid gap-px overflow-hidden rounded-sm ring-1 ring-border"
            >
              {#each cabinets as cabinet (cabinet.from)}
                <li
                  class="grid items-baseline gap-x-3 border-s-4 bg-card px-2.5 py-1.5 text-[0.8125rem] shadow-[0_0_0_0.5px_var(--border)] sm:grid-cols-[minmax(0,1fr)_auto]"
                  style:border-inline-start-color={swatch(
                    info.get(cabinet.party ?? "")?.color ?? null,
                    `party:${cabinet.party ?? ""}`,
                  )}
                >
                  <span class="truncate">
                    <span class="font-semibold">{cabinet.name}</span>
                    {#if cabinet.head != null}
                      <span class="text-muted-foreground"
                        >{m.record_governments_head({
                          name: cabinet.head,
                        })}</span
                      >
                    {/if}
                    {#if cabinet.isCaretaker}
                      <span class="ms-1 highlighter px-1 text-xs font-semibold"
                        >{m.governments_caretaker()}</span
                      >
                    {/if}
                  </span>
                  <span
                    class="font-figures text-xs text-muted-foreground tabular-nums"
                    >{date(cabinet.from)} – {cabinet.to == null
                      ? m.governments_present()
                      : date(cabinet.to)}</span
                  >
                </li>
              {/each}
            </ol>
          </div>
        {/if}
        {#if members.data?.isSeated === true}
          <Roster
            mode="changes"
            members={roll}
            parties={crew}
            start={members.data.start}
            end={members.data.end}
          />
        {/if}
      </section>

      {#if roll.length > 0 && council == null}
        <Roster
          mode="all"
          members={roll}
          parties={crew}
          present={members.data?.isSeated === true ? new Set(sitting) : null}
          start={members.data?.start ?? null}
          end={members.data?.end ?? null}
          title={members.data?.isSeated === true
            ? m.chamber_members_title({ span: span(term.start, term.end) })
            : m.chamber_members_returned({ span: span(term.start, term.end) })}
        />
      {/if}
    {/if}
    {#if council != null}
      <Council {council} hasTenures={body.tenures > 0} name={body.name} />
    {/if}
  </article>
</main>
