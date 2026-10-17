<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import { Tooltip as TooltipPrimitive } from "bits-ui";
  import Download from "#lib/components/download.svelte";
  import Name from "#lib/components/name.svelte";
  import { Input } from "#lib/components/ui/input/index.js";
  import * as Tooltip from "#lib/components/ui/tooltip/index.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { provenance } from "#lib/provenance.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  let {
    places,
    span,
    publishers,
    votes,
    isMapped = false,
    limit = 100,
    trace = null,
    only = $bindable(null),
    focus = $bindable(null),
  }: {
    places: NonNullable<
      Extract<PageData, { kind: "contest" }>["contest"]["places"]
    >;
    span: {
      from: string;
      to: string;
      forward: boolean;
      race: string | null;
    } | null;
    publishers: Array<string>;
    votes: string;
    isMapped?: boolean;
    limit?: number;
    trace?: string | null;
    only?: number | string | null;
    focus?: number | null;
  } = $props();

  const uid = $props.id();

  let query = $state("");
  let column = $state<"name" | "leader" | "margin" | "valid" | "shift">("name");
  let descending = $state(false);
  let all = $state(false);

  const noun = $derived(
    places.levels.find((level) => level.code === places.level)?.noun ?? "",
  );
  const heading = $derived(noun.charAt(0).toUpperCase() + noun.slice(1));
  const titles = $derived(
    places.legend.map((item) =>
      item.person == null ? (item.short ?? item.label) : item.label,
    ),
  );
  const colors = $derived(
    places.legend.map((item) => swatch(item.color, item.key)),
  );
  const leads = $derived(
    Map.groupBy(
      places.rows.filter((row) => row.leader != null),
      (row) => row.leader ?? 0,
    )
      .entries()
      .map(([index, rows]) => [index, rows.length] as const)
      .toArray()
      .toSorted((a, b) => b[1] - a[1]),
  );
  const comparing = $derived(
    span != null && places.rows.some((row) => row.earlier != null),
  );
  const listed = $derived([...places.rows, ...(places.districts ?? [])]);
  const owner = $derived.by(() => {
    const head = /^.+?'s /u.exec(places.rows[0]?.name ?? "")?.[0];
    return head != null &&
      places.rows.length > 1 &&
      places.rows.every((row) => row.name.startsWith(head))
      ? head
      : null;
  });
  const first = $derived(places.legend[0]);
  const second = $derived(places.legend[1]);
  const moves = $derived(
    listed.map((row) => {
      const before = span?.forward === true ? row.shares : row.earlier;
      const after = span?.forward === true ? row.earlier : row.shares;
      const from = span?.forward === true ? row.leader : row.prior;
      const to = span?.forward === true ? row.prior : row.leader;
      return {
        before,
        after,
        from,
        to,
        shift:
          before == null || after == null || second == null
            ? null
            : (after[0] ?? 0) -
              (after[1] ?? 0) -
              ((before[0] ?? 0) - (before[1] ?? 0)),
        flip: from == null || to == null || from === to ? null : { from, to },
      };
    }),
  );
  const tether = TooltipPrimitive.createTether<number>();
  const flips = $derived(moves.filter((move) => move.flip != null).length);
  const widest = $derived.by(() => {
    const sizes = moves
      .map((move) => Math.abs(move.shift ?? 0))
      .toSorted((a, b) => a - b);
    return Math.max(0.0001, sizes[Math.floor(sizes.length * 0.9)] ?? 0);
  });
  const filtered = $derived.by(() => {
    const [needle = "", ...names] = [
      query.trim(),
      ...listed.map((row) => row.name),
    ].map((text) =>
      text
        .replaceAll("ı", "i")
        .toLowerCase()
        .normalize("NFKD")
        .replaceAll(/\p{M}/gu, ""),
    );
    const isNested = column === "name" && !descending;
    return listed
      .map((row, index) => ({
        row,
        move: moves[index],
        value:
          column === "leader"
            ? row.leader == null
              ? -1
              : (row.shares[row.leader] ?? -1)
            : column === "margin"
              ? (row.margin ?? -1)
              : column === "valid"
                ? row.valid
                : (moves[index]?.shift ?? -Infinity),
        match:
          (names[index] ?? "").includes(needle) &&
          (only == null ||
            (only === "flip" && moves[index]?.flip != null) ||
            only === row.leader ||
            only === `${moves[index]?.flip?.from}>${moves[index]?.flip?.to}`),
      }))
      .filter((entry) => entry.match)
      .toSorted((a, b) => {
        const order =
          column === "name"
            ? a.row.name.localeCompare(b.row.name, undefined, { numeric: true })
            : a.value - b.value;
        return descending ? -order : order;
      })
      .map((entry) => {
        const parent = places.rows.find((row) => row.k === entry.row.within);
        const isUnder = isNested && parent != null;
        const prefix = isUnder ? `${parent.name}'s ` : owner;
        const short =
          prefix != null && entry.row.name.startsWith(prefix)
            ? entry.row.name
                .slice(prefix.length)
                .replace(" congressional district", " district")
            : entry.row.name;
        return {
          ...entry.row,
          move: entry.move,
          isNested: isUnder,
          label: short.charAt(0).toUpperCase() + short.slice(1),
        };
      });
  });
  const visible = $derived(all ? filtered : filtered.slice(0, limit));
  const levels = $derived(
    places.levels.map((level) => ({
      ...level,
      label: level.noun.charAt(0).toUpperCase() + level.noun.slice(1),
      query: new URLSearchParams({
        ...Object.fromEntries(page.url.searchParams),
        by: level.code,
      })
        .toString()
        .replaceAll("%2F", "/"),
    })),
  );
  const columns = $derived([
    { key: "name" as const, label: heading, numeric: false },
    { key: "leader" as const, label: m.col_leader(), numeric: false },
    {
      key: "margin" as const,
      label: m.col_margin(),
      numeric: true,
      definition: m.term_margin(),
    },
    { key: "valid" as const, label: votes, numeric: true },
    ...(comparing && span != null
      ? [
          {
            key: "shift" as const,
            label: m.col_shift(),
            numeric: true,
            definition: m.shift_title({
              first: titles[0] ?? "",
              second: titles[1] ?? "",
              from: span.from,
              to: span.to,
            }),
          },
        ]
      : []),
  ]);
  const foreign = $derived(
    places.sources.filter((publisher) => !publishers.includes(publisher)),
  );
</script>

{#snippet cite(
  text: string,
  measure: "votes" | "share" | "margin",
  row: (typeof visible)[number],
  label: string,
)}
  <button
    type="button"
    class="relative z-1 cursor-help underline decoration-transparent decoration-dotted underline-offset-4 hover:decoration-current focus-visible:decoration-current"
    onclick={() => {
      provenance.value = {
        label: `${row.name}, ${label.toLocaleLowerCase()}`,
        figure: text,
        measure,
        path: trace,
        key:
          measure === "share" && row.leader != null
            ? (places.legend[row.leader]?.key ?? null)
            : null,
        area: row.k,
      };
      provenance.isOpen = true;
    }}>{text}</button
  >
{/snippet}

{#snippet about(row: (typeof visible)[number] | undefined)}
  {#if row?.move?.before != null && row.move.after != null && row.move.shift != null && span != null}
    {const move = $derived(row.move)}
    {const toward = $derived(row.move.shift >= 0 ? first : second)}
    <Tooltip.Content
      collisionPadding={8}
      side="left"
      class="grid gap-1.5 leading-snug inline-[min(19rem,calc(100vw-2rem))]"
    >
      <span class="font-semibold">{row.name}</span>
      <span
        class="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-3 gap-y-0.5 text-xs"
      >
        <span></span>
        <span class="text-end font-figures opacity-70">{span.from}</span>
        <span class="text-end font-figures opacity-70">{span.to}</span>
        {#each new Set([0, 1, move.from ?? 0, move.to ?? 0]) as index (index)}
          {#if places.legend[index] != null}
            <span class="flex items-baseline gap-1.5 min-inline-0">
              <span
                class="shrink-0 self-center rounded-xs block-2 inline-2"
                style:background-color={colors[index]}
              ></span>
              <span class="truncate">{titles[index]}</span>
            </span>
            <span
              class={cn("text-end font-figures tabular-nums", {
                "font-semibold": index === move.from,
              })}>{percent(move.before?.[index] ?? 0)}</span
            >
            <span
              class={cn("text-end font-figures tabular-nums", {
                "font-semibold": index === move.to,
              })}>{percent(move.after?.[index] ?? 0)}</span
            >
          {/if}
        {/each}
      </span>
      <span class="text-xs opacity-80"
        >{m.fact_shift({
          points: points(Math.abs(move.shift ?? 0)).replace("+", ""),
          name: (toward === first ? titles[0] : titles[1]) ?? "",
        })}</span
      >
      {#if move.flip != null}
        <span class="flex flex-wrap items-baseline gap-x-1.5 text-xs">
          <span class="font-semibold">{m.places_flipped()}</span>
          <span class="opacity-80"
            >{m.flip_label({
              from: titles[move.flip.from] ?? "",
              to: titles[move.flip.to] ?? "",
            })}</span
          >
        </span>
      {/if}
    </Tooltip.Content>
  {/if}
{/snippet}

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-2"
  aria-labelledby="{uid}-title"
  data-places
>
  <div class="flex flex-wrap items-center gap-x-4 gap-y-2.5">
    <h2 id="{uid}-title" class="heading">
      {m.places_title({ noun })}
    </h2>
    {#if levels.length > 1 && !isMapped}
      <div class="flex" role="group" aria-label={m.places_level()}>
        {#each levels as level (level.code)}
          <a
            class={cn(
              "-ms-px flex items-center gap-1.5 border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
              {
                "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                  level.code === places.level,
              },
            )}
            href={resolve(`/[...path]?${level.query}`, {
              path: page.url.pathname.slice(1),
            })}
            aria-current={level.code === places.level ? "true" : undefined}
            data-sveltekit-reset="false"
            >{level.label}
            <span class="font-figures text-[0.6875rem] font-normal tabular-nums"
              >{level.count}</span
            ></a
          >
        {/each}
      </div>
    {/if}
    {#if places.rows.length > 12}
      <Input
        class="ms-auto block-8 inline-56 max-sm:inline-full"
        type="search"
        placeholder={m.places_filter()}
        aria-label={m.places_filter()}
        bind:value={query}
      />
    {/if}
    <Download
      class={cn({ "ms-auto": places.rows.length <= 12 })}
      name="places"
      columns={[
        heading,
        votes,
        m.col_leader(),
        `${m.col_margin()} (%)`,
        ...titles.map((title) => `${title} (%)`),
        ...(comparing ? [`${m.col_shift()} (%)`] : []),
        "URL",
      ]}
      rows={() =>
        listed.map((row, index) => [
          row.name,
          row.valid,
          row.leader == null ? null : (titles[row.leader] ?? null),
          row.margin == null ? null : Math.round(row.margin * 1e6) / 1e4,
          ...titles.map((_title, at) =>
            row.shares[at] == null
              ? null
              : Math.round((row.shares[at] ?? 0) * 1e6) / 1e4,
          ),
          ...(comparing
            ? [moves[index]?.shift ?? null].map((value) =>
                value == null ? null : Math.round(value * 1e6) / 1e4,
              )
            : []),
          row.path == null ? null : `${page.url.origin}/${row.path}`,
        ])}
    />
  </div>
  {#if span != null && !comparing}
    <p class="-mbs-1.5 text-[0.8125rem] text-muted-foreground">
      {m.places_no_earlier({ year: span.forward ? span.to : span.from })}
    </p>
  {/if}

  <div class="flex flex-wrap gap-1.5">
    {#each isMapped ? [] : leads as [index, won] (index)}
      <button
        type="button"
        class={cn(
          "flex items-center gap-1.5 rounded-xs px-1.5 text-[0.8125rem] block-7 hover:bg-secondary",
          { "marked bg-secondary": only === index },
        )}
        style:--party={colors[index]}
        aria-pressed={only === index}
        aria-label={m.places_leads({ label: titles[index] ?? "", count: won })}
        onclick={() => {
          only = only === index ? null : index;
          all = false;
        }}
      >
        <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
        <span class="font-semibold">{titles[index]}</span>
        <span class="font-figures text-xs text-muted-foreground tabular-nums"
          >{won}</span
        >
      </button>
    {/each}
    {#if comparing && flips > 0}
      <Tooltip.Root disableCloseOnTriggerClick delayDuration={400}>
        <Tooltip.Trigger>
          {#snippet child({ props })}
            <button
              {...props}
              type="button"
              class={cn(
                "flex items-center gap-1.5 rounded-sm border border-input bg-card px-2 text-[0.8125rem] keycap block-7 hover:bg-secondary",
                { "marked bg-secondary": only === "flip" },
              )}
              aria-pressed={only === "flip"}
              onclick={() => {
                only = only === "flip" ? null : "flip";
                all = false;
              }}
            >
              <span class="highlighter px-0.5 font-semibold"
                >{m.places_flipped()}</span
              >
              <span
                class="font-figures text-xs text-muted-foreground tabular-nums"
                >{flips}</span
              >
            </button>
          {/snippet}
        </Tooltip.Trigger>
        <Tooltip.Content collisionPadding={8} class="leading-snug max-inline-64"
          >{m.term_flips()}</Tooltip.Content
        >
      </Tooltip.Root>
    {/if}
  </div>

  <div class="-mx-2 overflow-x-auto">
    <table class="border-collapse text-sm/tight inline-full">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          {#each columns as entry (entry.key)}
            <th
              class={cn("px-2 py-1.5 font-medium", {
                "text-end": entry.numeric,
                "text-start": !entry.numeric,
                "max-sm:hidden": entry.key === "valid",
                "max-md:hidden": entry.key === "margin" && comparing,
              })}
              scope="col"
              aria-sort={column === entry.key
                ? descending
                  ? "descending"
                  : "ascending"
                : undefined}
            >
              <Tooltip.Root
                disabled={!("definition" in entry)}
                disableCloseOnTriggerClick
                delayDuration={400}
              >
                <Tooltip.Trigger>
                  {#snippet child({ props })}
                    <button
                      {...props}
                      type="button"
                      class={cn(
                        "inline-flex items-center gap-1 rounded-xs hover:text-foreground",
                        {
                          "font-semibold text-foreground": column === entry.key,
                        },
                      )}
                      aria-label={m.sort_by({ column: entry.label })}
                      onclick={() => {
                        if (column === entry.key) {
                          descending = !descending;
                        } else {
                          column = entry.key;
                          descending = entry.key !== "name";
                        }
                      }}
                    >
                      {entry.label}
                      {#if entry.key === "shift" && span != null}
                        <span
                          class="flex items-center gap-1 font-figures text-[0.6875rem] font-normal"
                          >{span.from}→{span.to}
                          <span
                            class="flex items-center gap-px"
                            aria-hidden="true"
                          >
                            <span class="text-[0.625rem]">◀</span>
                            <span
                              class="rounded-xs block-2 inline-2"
                              style:background-color={swatch(
                                second?.color,
                                second?.key ?? "",
                              )}
                            ></span>
                            <span
                              class="rounded-xs block-2 inline-2"
                              style:background-color={swatch(
                                first?.color,
                                first?.key ?? "",
                              )}
                            ></span>
                            <span class="text-[0.625rem]">▶</span>
                          </span>
                        </span>
                      {/if}
                      {#if column === entry.key}
                        {#if descending}
                          <ArrowDownIcon class="block-3 inline-3" />
                        {:else}
                          <ArrowUpIcon class="block-3 inline-3" />
                        {/if}
                      {/if}
                    </button>
                  {/snippet}
                </Tooltip.Trigger>
                {#if "definition" in entry}
                  <Tooltip.Content
                    collisionPadding={8}
                    class="leading-snug max-inline-64"
                    >{entry.definition}</Tooltip.Content
                  >
                {/if}
              </Tooltip.Root>
            </th>
            {#if entry.key === "name"}
              <th
                class="px-2 py-1.5 text-start font-medium inline-[34%] max-sm:hidden"
                scope="col">{m.col_result()}</th
              >
            {/if}
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each visible as row, index (row.path ?? row.name)}
          <tr
            class={cn(
              "group relative border-be border-border/60 last:border-be-0 hover:bg-secondary/70",
              { "bg-secondary/70": focus === row.k },
            )}
            onpointerenter={() => (focus = row.k)}
            onpointerleave={() => (focus = null)}
          >
            <th
              class={cn("px-2 py-1.5 text-start font-normal", {
                "ps-6": row.isNested,
              })}
              scope="row"
            >
              {#if row.path == null}
                <span class="font-semibold">{row.label}</span>
              {:else}
                <a
                  class={cn(
                    "font-semibold group-hover:underline group-hover:decoration-border group-hover:underline-offset-4 before:absolute before:inset-0",
                    { "font-medium": row.isNested },
                  )}
                  href={resolve("/[...path]", { path: row.path })}
                  onclick={(event) => {
                    if (
                      !event.metaKey &&
                      !event.ctrlKey &&
                      !event.shiftKey &&
                      !event.altKey
                    ) {
                      event.currentTarget.dataset.scopeSource = "";
                    }
                  }}>{row.label}</a
                >
              {/if}
              {#if row.isRemainder}<span
                  class="ms-1.5 text-xs font-normal text-muted-foreground"
                  >{m.places_remainder()}</span
                >{/if}
              <span
                class="mbs-1.5 flex overflow-hidden rounded-xs bg-muted block-2 sm:hidden"
                aria-hidden="true"
              >
                {#each row.shares as share, index (index)}
                  <span
                    class="shrink-0 bg-(--party) not-last:border-e not-last:border-card"
                    style:--party={colors[index]}
                    style:inline-size="{share * 100}%"
                  ></span>
                {/each}
              </span>
            </th>
            <td class="px-2 py-1.5 max-sm:hidden">
              <span
                class="flex overflow-hidden rounded-xs bg-muted block-3"
                aria-hidden="true"
              >
                {#each row.shares as share, index (index)}
                  <span
                    class={cn(
                      "shrink-0 bg-(--party) not-last:border-e not-last:border-card",
                      {
                        "opacity-35":
                          only != null && only !== "flip" && only !== index,
                      },
                    )}
                    style:--party={colors[index]}
                    style:inline-size="{share * 100}%"
                  ></span>
                {/each}
              </span>
            </td>
            <td class="px-2 py-1.5 whitespace-nowrap">
              {#if row.leader != null}
                <span class="flex items-center gap-1.5">
                  <Name
                    class="relative z-1 truncate font-semibold max-inline-40"
                    label={titles[row.leader] ?? ""}
                    color={colors[row.leader]}
                    person={places.legend[row.leader]?.person}
                    party={places.legend[row.leader]?.party}
                  />
                  <span
                    class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                    >{@render cite(
                      percent(row.shares[row.leader] ?? 0),
                      "share",
                      row,
                      `${titles[row.leader] ?? ""} ${m.col_share()}`,
                    )}</span
                  >
                </span>
              {/if}
            </td>
            <td
              class={cn(
                "px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap tabular-nums",
                { "max-md:hidden": comparing },
              )}
            >
              {#if row.margin != null}
                {@render cite(
                  points(row.margin),
                  "margin",
                  row,
                  m.col_margin(),
                )}
              {/if}
            </td>
            <td
              class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums max-sm:hidden"
            >
              {@render cite(count(row.valid), "votes", row, votes)}
            </td>
            {#if comparing}
              <td class="px-2 py-1.5">
                {#if row.move?.shift != null}
                  {const shift = $derived(row.move.shift)}
                  {const toward = $derived(shift >= 0 ? first : second)}
                  {const flip = $derived(row.move.flip)}
                  <Tooltip.Trigger {tether} payload={index} tabindex={-1}>
                    {#snippet child({ props })}
                      <svelte:element
                        this={row.path == null ? "span" : "a"}
                        {...props}
                        type={undefined}
                        class="relative z-1 flex items-center justify-end gap-2"
                        href={row.path == null
                          ? undefined
                          : resolve("/[...path]", { path: row.path })}
                        style:--party={swatch(toward?.color, toward?.key ?? "")}
                      >
                        {#if flip != null}
                          <span
                            class="flex shrink-0 items-center gap-0.5 highlighter px-1"
                          >
                            <span class="sr-only"
                              >{m.flip_label({
                                from: titles[flip.from] ?? "",
                                to: titles[flip.to] ?? "",
                              })}</span
                            >
                            <span
                              class="rounded-xs block-2 inline-2"
                              style:background-color={colors[flip.from]}
                            ></span>
                            <span class="text-[0.6875rem]" aria-hidden="true"
                              >→</span
                            >
                            <span
                              class="rounded-xs block-2 inline-2"
                              style:background-color={colors[flip.to]}
                            ></span>
                          </span>
                        {/if}
                        <span
                          class="relative flex-1 block-3 max-inline-32 min-inline-16 max-md:hidden"
                          aria-hidden="true"
                        >
                          <span
                            class="absolute -inset-y-0.5 inset-s-1/2 bg-border inline-px"
                          ></span>
                          <span
                            class="absolute inset-bs-1/2 -translate-y-1/2 bg-(--party) block-0.75"
                            style:inset-inline-start={shift >= 0 ? "50%" : null}
                            style:inset-inline-end={shift < 0 ? "50%" : null}
                            style:inline-size="calc({Math.min(
                              1,
                              Math.abs(shift) / widest,
                            ) * 50}% - 3px)"
                          ></span>
                          <span
                            class={cn(
                              "absolute inset-bs-1/2 -translate-y-1/2 bg-(--party) block-2.5 inline-1.75",
                              {
                                "[clip-path:polygon(0_0,100%_50%,0_100%)]":
                                  shift >= 0,
                                "[clip-path:polygon(100%_0,0_50%,100%_100%)]":
                                  shift < 0,
                              },
                            )}
                            style:inset-inline-start={shift >= 0
                              ? `calc(50% + ${Math.min(1, Math.abs(shift) / widest) * 50}% - 7px)`
                              : null}
                            style:inset-inline-end={shift < 0
                              ? `calc(50% + ${Math.min(1, Math.abs(shift) / widest) * 50}% - 7px)`
                              : null}
                          ></span>
                        </span>
                        <span
                          class="flex shrink-0 items-center justify-end gap-1 font-figures text-[0.8125rem] tabular-nums inline-16"
                        >
                          <span
                            class="rounded-xs bg-(--party) block-2 inline-2 md:hidden"
                          ></span>
                          {points(Math.abs(shift))}
                        </span>
                      </svelte:element>
                    {/snippet}
                  </Tooltip.Trigger>
                {/if}
              </td>
            {/if}
          </tr>
        {:else}
          <tr>
            <td
              class="px-2 py-4 text-muted-foreground"
              colspan={columns.length + 1}
              >{m.places_filter_empty({ query })}</td
            >
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <Tooltip.Root {tether} delayDuration={250}>
    {#snippet children({ payload })}
      {@render about(payload == null ? undefined : visible.at(Number(payload)))}
    {/snippet}
  </Tooltip.Root>

  {#if !all && filtered.length > limit}
    <button
      type="button"
      class="flex items-center justify-center rounded-sm border border-input bg-card text-[0.8125rem] font-semibold keycap block-8 hover:bg-secondary"
      onclick={() => (all = true)}
      >{m.places_show_all({ count: count(filtered.length) })}</button
    >
  {/if}
  {#if foreign.length > 0}
    <p
      class="text-[0.8125rem] text-muted-foreground"
      data-source={foreign.join("; ")}
    >
      {m.places_sources({ publisher: foreign.join(", ") })}
    </p>
  {/if}
</section>
