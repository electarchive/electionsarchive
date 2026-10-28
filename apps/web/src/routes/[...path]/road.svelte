<script lang="ts">
  import type { Snippet } from "svelte";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  type Cycle = Extract<PageData, { kind: "contest" }>["cycle"];

  const {
    rounds,
    cycle,
    after = [],
    paint = new Map(),
    more,
  }: {
    rounds: Extract<PageData, { kind: "contest" }>["rounds"];
    cycle: Pick<Cycle, "primaries" | "general">;
    after?: Array<{
      id: string;
      label: string;
      date: string | null;
      path: string | null;
    }>;
    paint?: Map<string, string>;
    more?: Snippet;
  } = $props();

  const uid = $props.id();

  let isOpen = $state(false);

  const road = $derived.by(() => {
    const leaders = (winners: NonNullable<Cycle["general"]>["winners"]) =>
      winners.map((winner) => ({
        ...winner,
        key: winner.person ?? winner.party ?? winner.label,
      }));
    const party = cycle.primaries.flatMap((primary) => {
      const name = primary.party?.short ?? primary.party?.label ?? null;
      const isMinor =
        primary.isMinor &&
        !primary.isCurrent &&
        primary.runoff?.isCurrent !== true;
      return [
        {
          id: primary.id,
          date: primary.date,
          until: primary.until,
          label:
            name == null
              ? m.round_primary()
              : (primary.until == null ? m.cycle_primary : m.cycle_primaries)({
                  party: name,
                }),
          path: primary.path,
          isCurrent: primary.isCurrent,
          isMinor,
          leaders: leaders(primary.winners),
          local: primary.local,
        },
        ...(primary.runoff == null
          ? []
          : [
              {
                id: primary.runoff.id,
                date: primary.runoff.date,
                until: null,
                label:
                  name == null
                    ? m.round_runoff()
                    : m.cycle_party_runoff({ party: name }),
                path: primary.runoff.path,
                isCurrent: primary.runoff.isCurrent,
                isMinor,
                leaders: leaders(primary.runoff.winners),
                local: null,
              },
            ]),
      ];
    });
    const known = new Set(party.map((stage) => stage.id));
    const generals = rounds.filter((round) => round.kind === "general");
    const american = rounds.some((round) => round.path?.split("/")[0] === "us");
    const runoff = rounds.some((round) => round.kind === "runoff");
    const chained = rounds
      .filter((round) => !known.has(round.id))
      .map((round) => {
        const index = rounds.indexOf(round);
        const moved = round.leaders.filter((leader) =>
          ["won", "elected", "advanced"].includes(leader.outcome ?? ""),
        );
        return {
          id: round.id,
          kind: round.kind,
          date: round.date,
          until: null,
          label:
            round.kind === "primary"
              ? m.round_primary()
              : round.kind === "runoff"
                ? american
                  ? m.round_runoff()
                  : m.round_second()
                : round.kind === "indirect" && !runoff
                  ? rounds.length === 2
                    ? index === 0
                      ? m.round_electoral()
                      : m.round_indirect()
                    : m.round_ballot({ round: index + 1 })
                  : round.kind === "indirect"
                    ? m.round_first()
                    : round.kind === "general"
                      ? generals.length < 2
                        ? american || !runoff
                          ? m.round_general()
                          : m.round_first()
                        : generals.indexOf(round) === 0
                          ? m.round_first()
                          : generals.indexOf(round) === 1
                            ? m.round_second()
                            : m.round_number({
                                round: generals.indexOf(round) + 1,
                              })
                      : m.round_number({ round: index + 1 }),
          path: round.path,
          isCurrent: round.isCurrent,
          isMinor: false,
          leaders: moved.length > 0 ? moved : round.leaders.slice(0, 1),
          local: null,
        };
      });
    const general = cycle.general;
    const early = [
      ...party,
      ...chained.filter((stage) => stage.kind === "primary"),
    ];
    const late = [
      ...chained.filter((stage) => stage.kind !== "primary"),
      ...(general == null || rounds.some((round) => round.id === general.id)
        ? []
        : [
            {
              id: general.id,
              date: general.date,
              until: null,
              label: m.round_general(),
              path: general.path,
              isCurrent: general.isCurrent,
              isMinor: false,
              leaders: leaders(general.winners),
              local: general.local,
            },
          ]),
      ...after.map((stage) => ({
        ...stage,
        until: null,
        isCurrent: false,
        isMinor: false,
        leaders: [],
        local: null,
      })),
    ].toSorted((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
    const order = (a: (typeof early)[number], b: (typeof early)[number]) =>
      (a.date ?? "").localeCompare(b.date ?? "");
    return {
      main: early.filter((stage) => !stage.isMinor).toSorted(order),
      minor: early.filter((stage) => stage.isMinor).toSorted(order),
      late,
    };
  });

  const stages = $derived([
    ...road.main,
    ...(isOpen ? road.minor : []),
    ...road.late,
  ]);
  const year = $derived(
    stages
      .map((stage) => stage.until ?? stage.date ?? "")
      .toSorted()
      .at(-1)
      ?.slice(0, 4),
  );

  const when = (from: string | null, to: string | null) => {
    if (from == null) {
      return "";
    }
    const format = new Intl.DateTimeFormat(getLocale(), {
      month: "short",
      day: "numeric",
      year: from.startsWith(year ?? "") ? undefined : "numeric",
      timeZone: "UTC",
    });
    const start = new Date(`${from}T00:00:00Z`);
    return to == null
      ? format.format(start)
      : format.formatRange(start, new Date(`${to}T00:00:00Z`));
  };
</script>

{#snippet rail(isLast: boolean)}
  <span class="relative self-stretch" aria-hidden="true">
    {#if !isLast}
      <span
        class="absolute inset-s-0.75 inset-bs-0 -inset-be-4 bg-border inline-px"
      ></span>
    {/if}
  </span>
{/snippet}

{#snippet stage(entry: (typeof stages)[number], isLast: boolean)}
  {const at = $derived(stages.indexOf(entry))}
  {const isNew = $derived(
    at < 1 ||
      stages[at - 1]?.date !== entry.date ||
      stages[at - 1]?.until !== entry.until,
  )}
  {const isHere = $derived(
    stages.some(
      (other) =>
        other.isCurrent &&
        other.date === entry.date &&
        other.until === entry.until,
    ),
  )}
  <li
    class={cn("col-span-full grid grid-cols-subgrid items-baseline", {
      "mbs-1 first:mbs-0": isNew,
    })}
    aria-current={entry.isCurrent ? "step" : undefined}
  >
    <span class="relative self-stretch" aria-hidden="true">
      {#if !isLast}
        <span
          class={cn("absolute inset-s-0.75 -inset-be-4 bg-border inline-px", {
            "inset-bs-2.5": isNew,
            "inset-bs-0": !isNew,
          })}
        ></span>
      {/if}
      {#if isNew}
        <span
          class={cn(
            "absolute inset-s-0 inset-bs-1.5 rounded-full border block-2 inline-2",
            {
              "border-foreground bg-foreground": isHere,
              "border-muted-foreground bg-card": !isHere,
            },
          )}
        ></span>
      {/if}
    </span>
    {#if isNew}
      <time
        class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
        datetime={entry.date}>{when(entry.date, entry.until)}</time
      >
    {:else}
      <span></span>
    {/if}
    {#if entry.isCurrent || entry.path == null}
      <span
        class={cn("justify-self-start font-medium", {
          "-mx-0.5 bg-marker/45 px-0.5 font-semibold dark:bg-marker dark:text-[#15171a]":
            entry.isCurrent,
        })}>{entry.label}</span
      >
    {:else}
      <Preview
        class="justify-self-start font-medium underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={entry.path}>{entry.label}</Preview
      >
    {/if}
    {#if entry.leaders.length > 0 || (entry.local != null && entry.local.place !== "")}
      <span
        class="col-start-3 flex flex-wrap items-baseline gap-x-3.5 gap-y-0.5 sm:col-start-4"
      >
        {#each entry.leaders as leader (leader.key)}
          <span class="inline-flex items-baseline gap-x-1.5">
            <Name
              label={leader.label}
              color={(entry.isCurrent
                ? paint.get(leader.person ?? "")
                : undefined) ?? swatch(leader.color, leader.key)}
              person={leader.person}
              party={leader.party}
            />
            {#if leader.share != null}
              <span
                class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                >{percent(leader.share)}</span
              >
            {/if}
          </span>
        {/each}
        {#if entry.local != null && entry.local.place !== ""}
          {#if entry.local.path == null || entry.local.isCurrent}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{m.cycle_in_place({ place: entry.local.place })}</span
            >
          {:else}
            <Preview
              class="text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-foreground"
              path={entry.local.path}
              >{m.cycle_in_place({ place: entry.local.place })}</Preview
            >
          {/if}
        {/if}
      </span>
    {/if}
  </li>
{/snippet}

{#if road.main.length + road.minor.length + road.late.length > 1}
  <nav
    class="col-span-12 grid gap-2 bg-card px-3.5 py-3"
    aria-labelledby="{uid}-title"
  >
    <h2 id="{uid}-title" class="sr-only">{m.rounds_title()}</h2>
    <ol
      class="grid grid-cols-[0.5rem_max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm sm:grid-cols-[0.5rem_max-content_max-content_minmax(0,1fr)] sm:gap-x-4"
    >
      {#each road.main as entry (entry.id)}
        {@render stage(entry, stages.at(-1) === entry)}
      {/each}
      {#if isOpen}
        {#each road.minor as entry (entry.id)}
          {@render stage(entry, stages.at(-1) === entry)}
        {/each}
      {/if}
      {#if road.minor.length > 0}
        <li class="col-span-full grid grid-cols-subgrid items-baseline">
          {@render rail(road.late.length === 0)}
          <button
            type="button"
            class="col-start-3 inline-flex items-center gap-1 justify-self-start text-[0.8125rem] text-muted-foreground hover:text-foreground"
            aria-expanded={isOpen}
            onclick={() => (isOpen = !isOpen)}
          >
            {m.cycle_others()}
            <span class="font-figures tabular-nums"
              >{count(road.minor.length)}</span
            >
            <ChevronDownIcon
              class={cn("transition-transform block-3.5 inline-3.5", {
                "rotate-180": isOpen,
              })}
              aria-hidden="true"
            />
          </button>
        </li>
      {/if}
      {#each road.late as entry (entry.id)}
        {@render stage(entry, stages.at(-1) === entry)}
      {/each}
    </ol>
    {@render more?.()}
  </nav>
{/if}
