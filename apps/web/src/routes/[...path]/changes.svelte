<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import Term from "#lib/components/term.svelte";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    rows,
    span,
    valid,
    counted,
    places,
    picked,
    onpick,
  }: {
    rows: Extract<PageData, { kind: "contest" }>["contest"]["rows"];
    span: { from: string; to: string; forward: boolean; race: string | null };
    valid: { before: number; after: number };
    counted: string;
    places: Extract<PageData, { kind: "contest" }>["contest"]["places"];
    picked: number | string | null;
    onpick: (value: string | null) => void;
  } = $props();

  const cells = $derived(
    rows
      .filter((row) => !row.key.startsWith("kind:"))
      .map((row) => ({
        row,
        name:
          row.key.startsWith("party:") && row.short != null
            ? row.short
            : row.label,
        color: swatch(row.color, row.key),
        person: row.key.startsWith("person:") ? row.key.slice(7) : null,
        party: (row.current ?? row.earlier)?.party ?? null,
        before: span.forward ? row.current : row.earlier,
        after: span.forward ? row.earlier : row.current,
      })),
  );
  const leads = $derived(
    (["before", "after"] as const).map((side) => {
      const [top, next] = cells
        .filter((cell) => cell[side] != null)
        .toSorted((a, b) => (b[side]?.share ?? 0) - (a[side]?.share ?? 0));
      return top == null
        ? null
        : {
            key: top.row.key,
            name: top[side]?.label ?? top.name,
            party: top.name,
            color: top.color,
            ids: {
              person: top[side]?.person ?? null,
              party: top[side]?.party ?? top.party,
            },
            margin: (top[side]?.share ?? 0) - (next?.[side]?.share ?? 0),
          };
    }),
  );
  const lede = $derived.by(() => {
    const [first, second] = leads;
    if (first == null || second == null) {
      return null;
    }
    const margins = {
      from: span.from,
      to: span.to,
      first: points(first.margin).replace("+", ""),
      second: points(second.margin).replace("+", ""),
    };
    const same = first.name === second.name;
    const text =
      first.key === second.key
        ? m.lede_same({ ...margins, name: "\u00010\u0001" })
        : m.lede_changed({
            from: span.from,
            to: span.to,
            first: "\u00010\u0001",
            second: "\u00011\u0001",
            firstMargin: margins.first,
            secondMargin: margins.second,
          });
    const names =
      first.key === second.key
        ? [
            {
              label: same ? first.name : first.party,
              color: first.color,
              person: same ? first.ids.person : null,
              party: first.ids.party,
            },
          ]
        : [first, second].map((lead) => ({
            label: lead.name,
            color: lead.color,
            ...lead.ids,
          }));
    return { parts: text.split("\u0001"), names };
  });
  const both = $derived(
    cells.filter((cell) => cell.before != null && cell.after != null),
  );
  const pair = $derived(
    both
      .toSorted(
        (a, b) =>
          (b.before?.share ?? 0) +
          (b.after?.share ?? 0) -
          (a.before?.share ?? 0) -
          (a.after?.share ?? 0),
      )
      .slice(0, 2),
  );
  const margin = $derived.by(() => {
    const [a, b] = pair;
    if (
      a == null ||
      b == null ||
      (["before", "after"] as const).some((side) =>
        cells
          .filter((cell) => cell[side] != null)
          .toSorted((x, y) => (y[side]?.share ?? 0) - (x[side]?.share ?? 0))
          .slice(0, 2)
          .some((cell) => cell !== a && cell !== b),
      )
    ) {
      return null;
    }
    const sides = (["before", "after"] as const).map((side) => {
      const gap = (a[side]?.share ?? 0) - (b[side]?.share ?? 0);
      return { lead: gap >= 0 ? a : b, gap: Math.abs(gap) };
    });
    const shift =
      (a.after?.share ?? 0) -
      (b.after?.share ?? 0) -
      ((a.before?.share ?? 0) - (b.before?.share ?? 0));
    return { sides, shift: Math.abs(shift), toward: shift >= 0 ? a : b };
  });
  const movers = $derived(
    both
      .map((cell) => ({
        ...cell,
        change: (cell.after?.share ?? 0) - (cell.before?.share ?? 0),
      }))
      .filter((cell) => Math.abs(cell.change) >= 0.0005)
      .toSorted((a, b) => Math.abs(b.change) - Math.abs(a.change))
      .slice(0, 3),
  );
  const flips = $derived.by(() => {
    const compared = places?.rows.filter((row) => row.earlier != null) ?? [];
    if (places == null || compared.length === 0 || both.length === 0) {
      return null;
    }
    const changed = compared
      .map((row) => ({
        from: span.forward ? row.leader : row.prior,
        to: span.forward ? row.prior : row.leader,
      }))
      .filter(
        (entry) =>
          entry.from != null && entry.to != null && entry.from !== entry.to,
      );
    return {
      total: compared.length,
      count: changed.length,
      kinds: Map.groupBy(changed, (entry) => `${entry.from}>${entry.to}`)
        .entries()
        .map(([value, entries]) => ({
          value,
          from: places.legend[entries[0]?.from ?? -1],
          to: places.legend[entries[0]?.to ?? -1],
          count: entries.length,
        }))
        .toArray()
        .toSorted((a, b) => b.count - a.count),
    };
  });
  const facts = $derived(
    1 +
      Number(margin != null) +
      Number(movers.length > 0) +
      Number(flips != null),
  );
</script>

{#snippet line(
  year: string,
  lead: {
    name: string;
    color: string;
    person: string | null;
    party: string | null;
  },
  value: string,
)}
  <span class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
    >{year}</span
  >
  <Name
    class="truncate font-semibold min-inline-0"
    label={lead.name}
    color={lead.color}
    person={lead.person}
    party={lead.party}
  />
  <span class="text-end font-figures text-[0.8125rem] tabular-nums"
    >{value}</span
  >
{/snippet}

<section class="col-span-12 grid gap-px" aria-labelledby="changes-title">
  <div class="bg-card px-3.5 pbs-3 pbe-3">
    <h2 id="changes-title" class="text-[0.8125rem] text-muted-foreground">
      {#if span.race == null}
        {m.changes_title({ from: span.from, to: span.to })}
      {:else if span.forward}
        {m.changes_title_to_race({ ...span, race: span.race })}
      {:else}
        {m.changes_title_from_race({ ...span, race: span.race })}
      {/if}
    </h2>
    {#if lede != null}
      <p
        class="mbs-1 font-serif text-[1.1875rem]/snug font-[560] tracking-[-0.005em] text-balance max-inline-[64ch]"
      >
        {#each lede.parts as part, index (index)}
          {#if index % 2 === 1}
            {const entity = $derived(lede.names[Number(part)])}
            {#if entity != null}
              <Name {...entity} />
            {/if}
          {:else}
            {part}
          {/if}
        {/each}
      </p>
    {/if}
    {#if span.race != null}
      <p class="mbs-1 text-[0.8125rem] text-muted-foreground">
        {m.note_different_race()}
      </p>
    {/if}
  </div>
  <dl class="grid grid-cols-12 gap-px">
    {#if margin != null}
      <div
        class={cn(
          "col-span-12 grid content-start gap-1.5 bg-card px-3.5 py-3",
          {
            "md:col-span-6": facts === 2,
            "md:col-span-4": facts === 3,
            "md:col-span-3": facts === 4,
          },
        )}
      >
        <dt class="text-[0.8125rem] text-muted-foreground">
          <Term label={m.fact_margin()} definition={m.term_margin()} />
        </dt>
        <dd
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1 text-sm/tight"
        >
          {#each margin.sides as side, index (index)}
            {@render line(
              index === 0 ? span.from : span.to,
              side.lead,
              points(side.gap),
            )}
          {/each}
        </dd>
        <dd class="text-[0.8125rem] text-muted-foreground">
          {const parts = $derived(
            m
              .fact_shift({
                points: points(margin.shift).replace("+", ""),
                name: "\u0001",
              })
              .split("\u0001"),
          )}
          {parts[0]}<Name
            class="text-foreground"
            label={margin.toward.name}
            color={margin.toward.color}
            person={margin.toward.person}
            party={margin.toward.party}
          />{parts[1]}
        </dd>
      </div>
    {/if}
    {#if movers.length > 0}
      <div
        class={cn(
          "col-span-12 grid content-start gap-1.5 bg-card px-3.5 py-3",
          {
            "md:col-span-6": facts === 2,
            "md:col-span-4": facts === 3,
            "md:col-span-3": facts === 4,
          },
        )}
      >
        <dt class="text-[0.8125rem] text-muted-foreground">
          {m.fact_movers()}
        </dt>
        <dd>
          <ul class="grid gap-1 text-sm/tight">
            {#each movers as mover (mover.row.key)}
              <li class="flex items-center gap-2" style:--party={mover.color}>
                <Name
                  class="flex-1 truncate font-semibold min-inline-0"
                  label={mover.name}
                  color={mover.color}
                  person={mover.person}
                  party={mover.party}
                />
                <span
                  class="relative block-1.5 inline-12 max-sm:hidden"
                  aria-hidden="true"
                >
                  <span
                    class="absolute -inset-y-0.5 inset-s-1/2 bg-border inline-px"
                  ></span>
                  <span
                    class="absolute inset-y-0 rounded-xs bg-(--party)"
                    style:inset-inline-start={mover.change >= 0 ? "50%" : null}
                    style:inset-inline-end={mover.change < 0 ? "50%" : null}
                    style:inline-size="{(Math.abs(mover.change) /
                      Math.max(0.0001, Math.abs(movers[0]?.change ?? 0))) *
                      50}%"
                  ></span>
                </span>
                <span
                  class="text-end font-figures text-[0.8125rem] tabular-nums inline-11"
                  >{points(mover.change)}</span
                >
              </li>
            {/each}
          </ul>
        </dd>
      </div>
    {/if}
    <div
      class={cn("col-span-12 grid content-start gap-1.5 bg-card px-3.5 py-3", {
        "md:col-span-6": facts === 2,
        "md:col-span-4": facts === 3,
        "md:col-span-3": facts === 4,
        "md:col-span-12": facts === 1,
      })}
    >
      <dt class="text-[0.8125rem] text-muted-foreground">{counted}</dt>
      <dd
        class="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 font-figures text-[0.8125rem] tabular-nums"
      >
        <span class="text-muted-foreground">{span.from}</span>
        <span class="text-end text-muted-foreground">{count(valid.before)}</span
        >
        <span class="text-muted-foreground">{span.to}</span>
        <span class="text-end font-semibold">{count(valid.after)}</span>
      </dd>
      {#if valid.before > 0}
        <dd
          class="text-end font-figures text-[0.8125rem] text-muted-foreground"
        >
          {new Intl.NumberFormat(getLocale(), {
            style: "percent",
            signDisplay: "exceptZero",
            maximumFractionDigits: 1,
          }).format(valid.after / valid.before - 1)}
        </dd>
      {/if}
    </div>
    {#if flips != null}
      <div
        class={cn(
          "col-span-12 grid content-start gap-1.5 bg-card px-3.5 py-3",
          {
            "md:col-span-6": facts === 2,
            "md:col-span-4": facts === 3,
            "md:col-span-3": facts === 4,
          },
        )}
      >
        <dt class="text-[0.8125rem] text-muted-foreground">
          <Term label={m.fact_flips()} definition={m.term_flips()} />
        </dt>
        <dd class="font-figures text-[0.9375rem] tabular-nums">
          {flips.count === 0
            ? m.fact_flips_none()
            : m.fact_flips_count({
                count: count(flips.count),
                total: count(flips.total),
              })}
        </dd>
        {#if flips.kinds.length > 0}
          <dd class="flex flex-wrap gap-1.5">
            {#each flips.kinds as kind (kind.value)}
              {const label = $derived(
                m.flip_label({
                  from: kind.from?.label ?? "",
                  to: kind.to?.label ?? "",
                }),
              )}
              <Tooltip.Root disableCloseOnTriggerClick delayDuration={250}>
                <Tooltip.Trigger>
                  {#snippet child({ props })}
                    <button
                      {...props}
                      type="button"
                      class={cn(
                        "flex items-center gap-1 rounded-sm border border-input bg-card px-1.5 text-[0.8125rem] keycap block-7 hover:bg-secondary",
                        { "marked bg-secondary": picked === kind.value },
                      )}
                      aria-pressed={picked === kind.value}
                      aria-label={label}
                      onclick={() =>
                        onpick(picked === kind.value ? null : kind.value)}
                    >
                      <span
                        class="rounded-xs block-2.5 inline-2.5"
                        style:background-color={swatch(
                          kind.from?.color,
                          kind.from?.key ?? "",
                        )}
                      ></span>
                      <span class="text-muted-foreground" aria-hidden="true"
                        >→</span
                      >
                      <span
                        class="rounded-xs block-2.5 inline-2.5"
                        style:background-color={swatch(
                          kind.to?.color,
                          kind.to?.key ?? "",
                        )}
                      ></span>
                      <span class="ms-0.5 font-figures text-xs tabular-nums"
                        >{kind.count}</span
                      >
                    </button>
                  {/snippet}
                </Tooltip.Trigger>
                <Tooltip.Content
                  collisionPadding={8}
                  class="leading-snug max-inline-64">{label}</Tooltip.Content
                >
              </Tooltip.Root>
            {/each}
          </dd>
        {/if}
      </div>
    {/if}
  </dl>
</section>
