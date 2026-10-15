<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowDownIcon from "@lucide/svelte/icons/arrow-down";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import Name from "#lib/components/name.svelte";
  import { Input } from "#lib/components/ui/input/index.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  let {
    places,
    span,
    publishers,
    votes,
    only = $bindable(null),
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
    only?: number | string | null;
  } = $props();

  const LIMIT = 100;

  let query = $state("");
  let column = $state<"name" | "leader" | "margin" | "valid" | "shift">("name");
  let descending = $state(false);
  let all = $state(false);

  const noun = $derived(
    places.levels.find((level) => level.code === places.level)?.noun ?? "",
  );
  const heading = $derived(noun.charAt(0).toUpperCase() + noun.slice(1));
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
  const first = $derived(places.legend[0]);
  const second = $derived(places.legend[1]);
  const moves = $derived(
    places.rows.map((row) => {
      const before = span?.forward === true ? row.shares : row.earlier;
      const after = span?.forward === true ? row.earlier : row.shares;
      const from = span?.forward === true ? row.leader : row.prior;
      const to = span?.forward === true ? row.prior : row.leader;
      return {
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
      ...places.rows.map((row) => row.name),
    ].map((text) =>
      text
        .replaceAll("ı", "i")
        .toLowerCase()
        .normalize("NFKD")
        .replaceAll(/\p{M}/gu, ""),
    );
    return places.rows
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
            ? a.row.name.localeCompare(b.row.name)
            : a.value - b.value;
        return descending ? -order : order;
      })
      .map((entry) => ({ ...entry.row, move: entry.move }));
  });
  const visible = $derived(all ? filtered : filtered.slice(0, LIMIT));
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
    { key: "margin" as const, label: m.col_margin(), numeric: true },
    { key: "valid" as const, label: votes, numeric: true },
    ...(comparing
      ? [{ key: "shift" as const, label: m.col_shift(), numeric: true }]
      : []),
  ]);
  const foreign = $derived(
    places.sources.filter((publisher) => !publishers.includes(publisher)),
  );
</script>

<section
  class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-2"
  aria-labelledby="places-title"
>
  <div class="flex flex-wrap items-center gap-x-4 gap-y-2.5">
    <h2
      id="places-title"
      class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
    >
      {m.places_title({ noun })}
    </h2>
    {#if levels.length > 1}
      <div class="flex" role="group" aria-label={m.places_level()}>
        {#each levels as level (level.code)}
          <a
            class={cn(
              "-ms-px flex keycap items-center gap-1.5 border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
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
            <span class="font-mono text-[0.6875rem] font-normal tabular-nums"
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
  </div>
  {#if span != null && !comparing}
    <p class="-mbs-1.5 text-[0.8125rem] text-muted-foreground">
      {m.places_no_earlier({ year: span.forward ? span.to : span.from })}
    </p>
  {/if}

  <div class="flex flex-wrap gap-1.5">
    {#each leads as [index, won] (index)}
      <button
        type="button"
        class={cn(
          "flex keycap items-center gap-1.5 rounded-sm border border-input bg-card ps-1.5 pe-2 text-[0.8125rem] block-7 hover:bg-secondary",
          { "marked bg-secondary": only === index },
        )}
        style:--party={colors[index]}
        aria-pressed={only === index}
        aria-label={m.places_leads({
          label: places.legend[index]?.label ?? "",
          count: won,
        })}
        onclick={() => {
          only = only === index ? null : index;
          all = false;
        }}
      >
        <span class="rounded-xs bg-(--party) block-2.5 inline-2.5"></span>
        <span class="font-semibold"
          >{places.legend[index]?.short ?? places.legend[index]?.label}</span
        >
        <span class="font-mono text-xs text-muted-foreground tabular-nums"
          >{won}</span
        >
      </button>
    {/each}
    {#if comparing && flips > 0}
      <button
        type="button"
        class={cn(
          "flex keycap items-center gap-1.5 rounded-sm border border-input bg-card px-2 text-[0.8125rem] block-7 hover:bg-secondary",
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
        <span class="font-mono text-xs text-muted-foreground tabular-nums"
          >{flips}</span
        >
      </button>
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
              <button
                type="button"
                class={cn(
                  "inline-flex items-center gap-1 rounded-xs hover:text-foreground",
                  { "font-semibold text-foreground": column === entry.key },
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
                    class="flex items-center gap-1 font-mono text-[0.6875rem] font-normal"
                    >{span.from}→{span.to}
                    <span class="flex items-center gap-px" aria-hidden="true">
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
        {#each visible as row (row.path ?? row.name)}
          <tr
            class="group relative border-be border-border/60 last:border-be-0 hover:bg-secondary/70"
          >
            <th class="px-2 py-1.5 text-start font-normal" scope="row">
              {#if row.path == null}
                <span class="font-semibold">{row.name}</span>
              {:else}
                <a
                  class="font-semibold group-hover:underline group-hover:decoration-border group-hover:underline-offset-4 before:absolute before:inset-0"
                  href={resolve("/[...path]", { path: row.path })}
                  onclick={(event) => {
                    document
                      .querySelector<HTMLElement>("[data-scope-title]")
                      ?.style.setProperty("view-transition-name", "none");
                    event.currentTarget.style.setProperty(
                      "view-transition-name",
                      "scope-title",
                    );
                  }}>{row.name}</a
                >
              {/if}
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
                    label={places.legend[row.leader]?.short ??
                      places.legend[row.leader]?.label ??
                      ""}
                    color={colors[row.leader]}
                    person={places.legend[row.leader]?.person}
                    party={places.legend[row.leader]?.party}
                  />
                  <span
                    class="font-mono text-[0.8125rem] text-muted-foreground tabular-nums"
                    >{percent(row.shares[row.leader] ?? 0)}</span
                  >
                </span>
              {/if}
            </td>
            <td
              class={cn(
                "px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap tabular-nums",
                { "max-md:hidden": comparing },
              )}
            >
              {row.margin == null ? "" : points(row.margin)}
            </td>
            <td
              class="px-2 py-1.5 text-end font-mono text-[0.8125rem] whitespace-nowrap text-muted-foreground tabular-nums max-sm:hidden"
            >
              {count(row.valid)}
            </td>
            {#if comparing}
              <td class="px-2 py-1.5">
                {#if row.move?.shift != null}
                  {const shift = $derived(row.move.shift)}
                  {const toward = $derived(shift >= 0 ? first : second)}
                  {const flip = $derived(row.move.flip)}
                  <span
                    class="flex items-center justify-end gap-2"
                    style:--party={swatch(toward?.color, toward?.key ?? "")}
                  >
                    {#if flip != null}
                      <span
                        class="flex shrink-0 items-center gap-0.5 highlighter px-1"
                        title={m.flip_label({
                          from: places.legend[flip.from]?.label ?? "",
                          to: places.legend[flip.to]?.label ?? "",
                        })}
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
                      class="flex shrink-0 items-center justify-end gap-1 font-mono text-[0.8125rem] tabular-nums inline-16"
                      title={m.shift_title({
                        first: first?.label ?? "",
                        second: second?.label ?? "",
                        from: span?.from ?? "",
                        to: span?.to ?? "",
                      })}
                    >
                      <span
                        class="rounded-xs bg-(--party) block-2 inline-2 md:hidden"
                      ></span>
                      {points(Math.abs(shift))}
                    </span>
                  </span>
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

  {#if !all && filtered.length > LIMIT}
    <button
      type="button"
      class="flex keycap items-center justify-center rounded-sm border border-input bg-card text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
      onclick={() => (all = true)}
      >{m.places_show_all({ count: count(filtered.length) })}</button
    >
  {/if}
  {#if foreign.length > 0}
    <p class="text-[0.8125rem] text-muted-foreground">
      {m.places_sources({ publisher: foreign.join(", ") })}
    </p>
  {/if}
</section>
