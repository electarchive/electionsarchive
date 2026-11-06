<script lang="ts">
  import type { ChamberView, Family, Seat } from "#lib/live/nation.js";
  import { percent } from "#lib/format.js";
  import { isChanging } from "#lib/live/flips.js";
  import { lead } from "#lib/live/format.js";
  import { allied, isSettled, lane, odds, seatsOf } from "#lib/live/nation.js";
  import { isContested } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import Flip from "./flip.svelte";

  const {
    view,
    sides,
    forecasts,
    title,
    label,
    href,
    tint,
    short,
    party,
    limit = null,
  }: {
    view: ChamberView;
    sides: [Family, Family];
    forecasts: boolean;
    title: string;
    label: (seat: Seat) => string;
    href: (seat: Seat) => string;
    tint: (id: string | null) => string;
    short: (id: string | null) => string;
    party: (id: string) => { short: string; color: string };
    limit?: number | null;
  } = $props();

  const left = $derived(sides[0]);
  const right = $derived(sides[1]);

  let isAll = $state(false);

  const GROUPS = ["left", "middle", "right"] as const;

  function groupOf(seat: Seat) {
    const where = lane(seat, sides);
    return where === "left" || where === "right" ? where : "middle";
  }

  const ordered = $derived(
    GROUPS.flatMap((group) =>
      view.order.filter((seat) => groupOf(seat) === group),
    ),
  );
  const tipping = $derived(
    view.needed > 0 && view.needed <= ordered.length
      ? ordered[view.needed - 1]
      : null,
  );
  const isLimited = $derived(limit != null && !isAll && ordered.length > limit);
  const shown = $derived.by(() => {
    if (limit == null || !isLimited) {
      return ordered;
    }
    const closest = new Set(
      ordered
        .map((seat) => ({
          seat,
          distance: isSettled(seat.rating)
            ? Number.POSITIVE_INFINITY
            : Math.abs(seat.lean) + (seat.summary.total === 0 ? 1 : 0),
        }))
        .toSorted((a, b) => a.distance - b.distance)
        .slice(0, limit)
        .map((entry) => entry.seat),
    );
    if (tipping != null) {
      closest.add(tipping);
    }
    return ordered.filter((seat) => closest.has(seat));
  });

  const middle = $derived.by(() => {
    const others = new Map<string, number>();
    let tossups = 0;
    let waiting = 0;
    for (const seat of ordered) {
      if (groupOf(seat) !== "middle") {
        continue;
      }
      const where = lane(seat, sides);
      if (where === "tossup") {
        tossups += 1;
      } else if (where === "other") {
        const id = seat.side ?? seat.party ?? "";
        others.set(id, (others.get(id) ?? 0) + 1);
      } else {
        waiting += 1;
      }
    }
    return [
      { key: "tossup", count: tossups, text: m.night_ladder_tossup() },
      ...[...others].map(([id, count]) => ({
        key: id,
        count,
        text: m.night_ladder_side({ party: short(id) }),
      })),
      ...(waiting > 0
        ? [{ key: "waiting", count: waiting, text: m.night_ladder_waiting() }]
        : []),
    ];
  });

  const blocks = $derived(
    GROUPS.map((group) => {
      const rows = shown.filter((seat) => groupOf(seat) === group);
      const total = ordered.filter((seat) => groupOf(seat) === group).length;
      const side = group === "left" ? left : group === "right" ? right : null;
      return {
        group,
        rows,
        side,
        count:
          group === "left" || group === "right"
            ? seatsOf(view.tallies[group])
            : 0,
        isCaucus: group !== "middle" && allied(view.blocs, group).length > 0,
        span:
          Math.max(1, rows.length) +
          (tipping != null && rows.includes(tipping) ? 1 : 0),
        empty: total > 0 ? m.night_ladder_none_close() : m.night_ladder_none(),
      };
    }),
  );

  function letter(id: string | null) {
    const name =
      id === left.id ? left.short : id === right.id ? right.short : short(id);
    return name.slice(0, 1);
  }

  function ink(id: string | null) {
    return `color-mix(in oklab, ${tint(id)} 78%, var(--foreground))`;
  }

  function chance(seat: Seat) {
    const { rating, summary } = seat;
    if (rating === "won") {
      return { text: m.night_ladder_won(), id: seat.side };
    }
    if (rating === "projected") {
      return { text: m.night_ladder_projected(), id: seat.side };
    }
    if (rating === "unopposed") {
      return { text: m.night_state_unopposed(), id: seat.side };
    }
    if (rating === "later") {
      return { text: m.night_later(), id: null };
    }
    if (rating === "waiting") {
      return { text: m.night_ladder_waiting(), id: null };
    }
    if (rating === "counting" || !forecasts) {
      return { text: m.night_ladder_counting(), id: null };
    }
    const isPair = seat.side === left.id || seat.side === right.id;
    const value = isPair ? Math.max(seat.left, seat.right) : summary.chance;
    if (value == null) {
      return { text: m.night_ladder_counting(), id: null };
    }
    const id = isPair
      ? seat.left >= seat.right
        ? left.id
        : right.id
      : seat.side;
    return {
      text: m.night_ladder_chance({ chance: odds(value), party: letter(id) }),
      id,
    };
  }

  function margin(seat: Seat) {
    if (forecasts && seat.band != null) {
      return {
        id: seat.band.mid >= 0 ? left.id : right.id,
        value: seat.band.mid,
        isEstimate: true,
      };
    }
    if (seat.summary.total === 0 || !isContested(seat.summary.race)) {
      return null;
    }
    if (
      seat.signed != null &&
      (seat.side === left.id || seat.side === right.id)
    ) {
      return {
        id: seat.signed >= 0 ? left.id : right.id,
        value: seat.signed,
        isEstimate: false,
      };
    }
    return seat.summary.leader == null
      ? null
      : { id: seat.side, value: seat.summary.margin, isEstimate: false };
  }

  const notes = $derived([
    ...(view.tallies.left.holdover + view.tallies.right.holdover > 0
      ? [m.night_ladder_holdovers()]
      : []),
    ...(["left", "right"] as const).flatMap((group) => {
      const names = allied(view.blocs, group).flatMap((bloc) => bloc.names);
      return names.length === 0
        ? []
        : [
            m.night_ladder_caucus({
              names: new Intl.ListFormat(getLocale(), {
                type: "conjunction",
              }).format(names),
              party: (group === "left" ? left : right).name,
            }),
          ];
    }),
    ...(shown.some((seat) => seat.summary.race.isSpecial === true)
      ? [`† ${m.night_ladder_special()}`]
      : []),
    ...(shown.some((seat) => seat.summary.race.needsMajority === true)
      ? [`* ${m.night_ladder_majority()}`]
      : []),
    ...(shown.some((seat) => isChanging(seat.shift))
      ? [m.night_ladder_flip_note()]
      : []),
    ...(shown.some((seat) => margin(seat)?.isEstimate === false)
      ? [m.night_ladder_count_note()]
      : []),
  ]);
</script>

<section class="@container grid content-start gap-2">
  <div class="grid gap-0.5">
    <h3 class="heading text-base!">{title}</h3>
    {#if view.needed <= 0}
      <p class="text-xs text-pretty text-muted-foreground">
        {m.night_ladder_secured({ party: left.name })}
      </p>
    {:else if isLimited}
      <p class="text-xs text-muted-foreground">
        {m.night_ladder_closest({ count: shown.length, total: ordered.length })}
      </p>
    {/if}
  </div>
  <div>
    <table
      class="table-fixed border-collapse text-[0.8125rem] inline-full [&_td]:py-1 [&_td]:align-baseline"
    >
      <colgroup>
        <col class="inline-19 @[30rem]:inline-30" />
        <col />
        <col class="inline-14 @max-[30rem]:hidden" />
        <col class="inline-19" />
        <col class="inline-17" />
      </colgroup>
      <thead>
        <tr
          class="text-[0.6875rem] font-semibold text-muted-foreground [&_th]:pbe-1 [&_th]:align-bottom [&_th]:shadow-[inset_0_-1px_0_var(--color-foreground)]"
        >
          <td></td>
          <th scope="col" class="ps-1.5 text-start"
            >{m.night_ladder_col_race()}</th
          >
          <th scope="col" class="text-end @max-[30rem]:hidden"
            >{m.night_ladder_col_in()}</th
          >
          <th scope="col" class="text-end">{m.night_ladder_col_chance()}</th>
          <th scope="col" class="pe-1.5 text-end"
            >{m.night_ladder_col_margin()}</th
          >
        </tr>
      </thead>
      {#each blocks as block (block.group)}
        {const color = $derived(
          block.side == null ? "var(--border)" : block.side.color,
        )}
        {#snippet rail()}
          <th
            scope="rowgroup"
            rowspan={block.span}
            class="relative pe-5 pbs-2 pbe-3 text-start align-top font-normal"
          >
            <span
              class={cn("absolute inset-e-2 inline-0.5", {
                "inset-bs-1.5 inset-be-0": block.group === "left",
                "inset-y-0": block.group === "middle",
                "inset-bs-0 inset-be-1.5": block.group === "right",
              })}
              style:background-color={color}
              aria-hidden="true"
            ></span>
            {#if block.group !== "middle"}
              <span
                class={cn(
                  "absolute inset-e-0.75 border-x-[5px] border-x-transparent",
                  {
                    "inset-bs-0 border-be-[7px]": block.group === "left",
                    "inset-be-0 border-bs-[7px]": block.group === "right",
                  },
                )}
                style:border-block-end-color={block.group === "left"
                  ? color
                  : undefined}
                style:border-block-start-color={block.group === "right"
                  ? color
                  : undefined}
                aria-hidden="true"
              ></span>
            {/if}
            <span class="sticky inset-bs-2 grid gap-2">
              {#if block.side == null}
                {#each middle as entry, index (entry.key)}
                  <span class="grid gap-0.5">
                    <span
                      class={cn("font-figures leading-none tabular-nums", {
                        "text-2xl font-semibold": index === 0,
                        "text-base font-semibold": index > 0,
                      })}
                      style:color={entry.key === "tossup" ||
                      entry.key === "waiting"
                        ? undefined
                        : ink(entry.key)}>{entry.count}</span
                    >
                    <span
                      class="text-[0.6875rem]/tight text-pretty text-muted-foreground"
                      >{entry.text}</span
                    >
                  </span>
                {/each}
              {:else}
                <span class="grid gap-0.5">
                  <span
                    class="font-figures text-2xl/none font-semibold tabular-nums"
                    style:color={ink(block.side.id)}>{block.count}</span
                  >
                  <span
                    class="text-[0.6875rem]/tight text-pretty text-muted-foreground"
                    >{block.isCaucus
                      ? m.night_ladder_caucus_side({ party: block.side.short })
                      : m.night_ladder_side({ party: block.side.short })}</span
                  >
                </span>
              {/if}
            </span>
          </th>
        {/snippet}
        <tbody
          class={cn({
            "shadow-[inset_0_1px_0_var(--color-foreground),inset_0_-1px_0_var(--color-foreground)]":
              block.group === "middle",
          })}
        >
          {#if block.rows.length === 0}
            <tr>
              {@render rail()}
              <td colspan="4" class="ps-1.5 text-xs text-muted-foreground"
                >{block.empty}</td
              >
            </tr>
          {/if}
          {#each block.rows as seat, index (seat.summary.race.id)}
            {const call = $derived(chance(seat))}
            {const gap = $derived(margin(seat))}
            {const counted = $derived(seat.summary.counted)}
            {const race = $derived(seat.summary.race)}
            <tr
              class="border-be border-border"
              style:background-color={block.side == null
                ? undefined
                : `color-mix(in oklab, ${block.side.color} 7%, var(--background))`}
            >
              {#if index === 0}
                {@render rail()}
              {/if}
              <td class="ps-1.5 min-inline-0">
                <span class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                  <a
                    href={href(seat)}
                    class="truncate font-semibold underline-offset-[0.2em] min-inline-0 hover:underline"
                    >{label(seat)}{#if race.isSpecial === true}<sup
                        class="ms-px font-normal"
                        title={m.night_ladder_special()}>†</sup
                      >{/if}{#if race.needsMajority === true}<sup
                        class="ms-px font-normal"
                        title={m.night_ladder_majority()}>*</sup
                      >{/if}</a
                  >
                  {#if seat.shift != null && seat.shift.flip !== "held"}
                    <Flip
                      flip={seat.shift.flip}
                      from={party(seat.shift.from)}
                      to={party(seat.shift.to)}
                      isCompact
                    />
                  {/if}
                </span>
                {#if counted != null}
                  <span
                    class="block font-figures text-[0.6875rem] text-muted-foreground tabular-nums @[30rem]:hidden"
                    >{m.night_ladder_in({ share: percent(counted, 0) })}</span
                  >
                {/if}
              </td>
              <td
                class="text-end font-figures text-muted-foreground tabular-nums @max-[30rem]:hidden"
                >{counted == null ? "" : percent(counted, 0)}</td
              >
              <td
                class={cn("text-end font-figures text-xs tabular-nums", {
                  "text-muted-foreground": call.id == null,
                  "font-semibold": isSettled(seat.rating),
                })}
                style:color={call.id == null ? undefined : ink(call.id)}
                >{call.text}</td
              >
              <td
                class={cn("pe-1.5 text-end font-figures text-xs tabular-nums", {
                  "text-muted-foreground": gap?.isEstimate === false,
                })}
                style:color={gap?.isEstimate === true ? ink(gap.id) : undefined}
                >{gap == null
                  ? ""
                  : m.night_ladder_margin({
                      party: letter(gap.id),
                      margin: lead(gap.value),
                    })}</td
              >
            </tr>
            {#if seat === tipping}
              <tr>
                <td colspan="4" class="p-0!">
                  <span
                    class="flex items-center gap-2 border-bs-2 border-dashed border-foreground ps-1.5 pbs-0.5 text-[0.6875rem] font-semibold"
                    title={m.night_ladder_tipping_title({ party: left.name })}
                    >{m.night_ladder_tipping()}</span
                  >
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      {/each}
    </table>
  </div>
  {#if limit != null && ordered.length > limit}
    <button
      type="button"
      class="justify-self-start text-xs font-semibold underline decoration-current/40 underline-offset-[0.2em] hover:decoration-current"
      onclick={() => (isAll = !isAll)}
      >{isAll
        ? m.night_ladder_fewer()
        : m.night_ladder_all({ count: ordered.length })}</button
    >
  {/if}
  {#if notes.length > 0}
    <ul class="grid gap-0.5 text-[0.6875rem] text-pretty text-muted-foreground">
      {#each notes as note (note)}
        <li>{note}</li>
      {/each}
    </ul>
  {/if}
</section>
