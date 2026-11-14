<script lang="ts">
  import CopyIcon from "@lucide/svelte/icons/copy";
  import { toast } from "svelte-sonner";
  import type { Release, ReleaseRow } from "#lib/server/downloads.js";
  import { copy } from "#lib/clipboard.js";
  import CiteBody from "#lib/components/cite-body.svelte";
  import ShareImage from "#lib/components/share-image.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { channelLabel, measureLabel } from "#lib/contrib.js";
  import {
    bytes,
    contestLabel,
    countryName,
    dataset,
    describe,
    electionLabel,
    formulaLabel,
    latestUrl,
    questionLabel,
    quickstart,
    SHARED_FILES,
    sharedLabel,
    sizes,
    span,
    stageLabel,
    tag,
    TOTAL_ORDER,
    totalLabel,
  } from "#lib/downloads.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { moment } from "#lib/site.js";
  import Changes from "./changes.svelte";
  import Countries from "./countries.svelte";
  import Files from "./files.svelte";

  const {
    origin,
    releases,
    release,
    isIndex,
  }: {
    origin: string;
    releases: Array<ReleaseRow>;
    release: Release | null;
    isIndex: boolean;
  } = $props();

  const path = $derived(
    isIndex || release == null ? "/downloads" : `/downloads/${release.calver}`,
  );
  const canonical = $derived(`${origin}${path}`);
  const title = $derived(
    isIndex || release == null
      ? m.downloads_title()
      : m.downloads_release_title({ version: release.calver }),
  );
  const description = $derived(
    release == null ? m.downloads_lede() : describe(release),
  );
  const summary = $derived(release?.summary ?? null);
  const previous = $derived(release?.previous ?? null);
  const end = $derived(
    Number(summary?.span.last?.slice(0, 4) ?? new Date().getUTCFullYear()),
  );
  const jsonld = $derived(
    release == null
      ? null
      : JSON.stringify(
          dataset({ origin, url: canonical, release, isSeries: isIndex }),
        )
          .replaceAll("<", String.raw`<`)
          .replaceAll("&", String.raw`&`),
  );
  const totals = $derived(
    summary == null
      ? []
      : TOTAL_ORDER.flatMap((key) => {
          const value = summary.totals[key];
          if (value == null) {
            return [];
          }
          const before = previous?.totals?.[key];
          return [
            { key, value, delta: before == null ? null : value - before },
          ];
        }),
  );
  const lead = $derived(totals.find((total) => total.key === "results"));
  const rest = $derived(totals.filter((total) => total.key !== "results"));
  const breakdowns = $derived(
    summary == null
      ? []
      : [
          {
            key: "contest-kinds",
            title: m.downloads_contest_kinds(),
            rows: summary.contest_kinds.map((row) => ({
              key: row.kind,
              label: contestLabel(row.kind),
              value: row.count,
            })),
          },
          {
            key: "election-kinds",
            title: m.downloads_election_kinds(),
            rows: summary.election_kinds.map((row) => ({
              key: row.kind,
              label: electionLabel(row.kind),
              value: row.count,
            })),
          },
          {
            key: "formulas",
            title: m.downloads_formulas(),
            rows: summary.formulas.map((row) => ({
              key: row.formula,
              label: formulaLabel(row.formula),
              value: row.contests,
            })),
          },
          {
            key: "questions",
            title: m.downloads_questions(),
            rows: summary.ballot_questions.map((row) => ({
              key: row.type,
              label: questionLabel(row.type),
              value: row.count,
            })),
          },
          {
            key: "stages",
            title: m.downloads_stages(),
            rows: summary.stages.map((row) => ({
              key: row.stage,
              label: stageLabel(row.stage),
              value: row.count,
            })),
          },
          {
            key: "measures",
            title: m.downloads_measures(),
            rows: summary.measures.map((row) => ({
              key: row.code,
              label: measureLabel(row.code),
              value: row.rows,
            })),
          },
          {
            key: "channels",
            title: m.downloads_channels(),
            rows: summary.channels
              .filter((row) => row.code !== "all")
              .map((row) => ({
                key: row.code,
                label: channelLabel(row.code),
                value: row.rows,
              })),
          },
        ].filter((panel) => panel.rows.length > 0),
  );
  const shapes = $derived.by(() => {
    const byCountry = new Map<
      string,
      {
        code: string;
        label: string;
        rows: NonNullable<typeof summary>["shapes"];
      }
    >();
    for (const shape of summary?.shapes ?? []) {
      const code = shape.country ?? "WORLD";
      const entry = byCountry.get(code) ?? {
        code,
        label: countryName(code),
        rows: [],
      };
      entry.rows.push(shape);
      byCountry.set(code, entry);
    }
    return [...byCountry.values()]
      .map((entry) => ({
        ...entry,
        rows: entry.rows.toSorted((a, b) => b.shapes - a.shapes),
      }))
      .toSorted((a, b) => a.label.localeCompare(b.label));
  });
  const bundles = $derived(release == null ? null : sizes(release.files));
  const steps = $derived(release == null ? [] : quickstart(release.calver));
  const sample = $derived(
    release?.files.find((file) => file.file.endsWith(".parquet"))?.file ??
      "manifest.json",
  );
  const sections = $derived(
    [
      { id: "contents", label: m.downloads_contents(), isShown: lead != null },
      {
        id: "countries",
        label: m.downloads_countries(),
        isShown: (summary?.countries.length ?? 0) > 0,
      },
      {
        id: "breakdown",
        label: m.downloads_breakdown(),
        isShown: breakdowns.length > 0,
      },
      { id: "shapes", label: m.downloads_shapes(), isShown: shapes.length > 0 },
      { id: "changes", label: m.downloads_changes(), isShown: true },
      { id: "files", label: m.downloads_files(), isShown: true },
      { id: "quickstart", label: m.downloads_quickstart(), isShown: true },
      { id: "licenses", label: m.downloads_licenses(), isShown: true },
      { id: "cite", label: m.downloads_cite(), isShown: true },
      {
        id: "releases",
        label: m.downloads_releases(),
        isShown: releases.length > 1,
      },
    ].filter((section) => section.isShown),
  );

  const signed = (value: number) =>
    `${value > 0 ? "+" : "−"}${count(Math.abs(value))}`;

  function copyText(text: string) {
    void copy(text).then(() => toast.success(m.downloads_copied()));
  }

  function top(rows: Array<{ key: string; label: string; value: number }>) {
    const sorted = rows.toSorted((a, b) => b.value - a.value);
    if (sorted.length <= 8) {
      return sorted;
    }
    let other = 0;
    for (const row of sorted.slice(7)) {
      other += row.value;
    }
    return [
      ...sorted.slice(0, 7),
      { key: "__other", label: m.downloads_other(), value: other },
    ];
  }
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <link rel="alternate" type="text/markdown" href="{canonical}.md" />
  <link rel="alternate" type="application/json" href="{canonical}.json" />
  <meta property="og:type" content="website" />
  <meta property="og:title" content={title} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  <meta name="twitter:title" content={title} />
  <meta name="twitter:description" content={description} />
  {#if jsonld != null}
    <svelte:element this={"script"} type="application/ld+json"
      >{jsonld}</svelte:element
    >
  {/if}
</svelte:head>

<ShareImage
  src="{origin}/og{path}.png"
  alt={m.downloads_share_alt({ title })}
/>

{#snippet code(text: string, label: string)}
  <div class="grid field grid-cols-[minmax(0,1fr)_auto] items-start">
    <pre
      class="overflow-x-auto px-3 py-2.5 font-figures text-[0.8125rem] leading-relaxed">{text}</pre>
    <Button
      variant="ghost"
      size="sm"
      class="m-1"
      aria-label="{m.downloads_copy()}: {label}"
      onclick={() => copyText(text)}
    >
      <CopyIcon />{m.downloads_copy()}
    </Button>
  </div>
{/snippet}

{#snippet delta(value: number | null)}
  {#if value != null && value !== 0 && previous != null}
    <span
      class="font-figures text-[0.75rem] text-muted-foreground tabular-nums"
      title={m.downloads_delta_title({ version: previous.calver })}
      >{signed(value)}<span class="sr-only">
        {m.downloads_delta_title({ version: previous.calver })}</span
      ></span
    >
  {/if}
{/snippet}

<main class="mx-auto grid gap-10 px-4 pbs-6 pbe-24 max-inline-6xl max-sm:px-3">
  <header class="grid gap-5 border-be-2 border-foreground pbe-6">
    <div class="grid gap-3 max-inline-[68ch]">
      <h1 class="title">{title}</h1>
      <p class="text-[1.0625rem] text-pretty">
        {release == null ? m.downloads_lede() : description}
      </p>
    </div>
    {#if release != null}
      <dl
        class="flex flex-wrap gap-x-8 gap-y-3 text-sm [&_dd]:font-figures [&_dd]:tabular-nums [&_dt]:text-[0.75rem] [&_dt]:text-muted-foreground"
      >
        <div class="grid">
          <dt>{m.downloads_version()}</dt>
          <dd class="font-semibold">{release.calver}</dd>
        </div>
        <div class="grid">
          <dt>{m.downloads_published_label()}</dt>
          <dd>{date(release.created_at.slice(0, 10))}</dd>
        </div>
        <div class="grid">
          <dt>{m.downloads_as_of_label()}</dt>
          <dd>{moment(release.upto)}</dd>
        </div>
        <div class="grid">
          <dt>{m.downloads_size_label()}</dt>
          <dd>
            {bytes(release.bytes)}
            <span class="text-muted-foreground"
              >{m.downloads_file_count({ count: release.files.length })}</span
            >
          </dd>
        </div>
        {#if release.doi != null}
          <div class="grid">
            <dt>DOI</dt>
            <dd>
              <a
                href="https://doi.org/{release.doi}"
                rel="external"
                class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                >{release.doi}</a
              >
            </dd>
          </div>
        {/if}
      </dl>
      <div class="flex flex-wrap gap-2">
        <Button href="#quickstart">{m.downloads_get()}</Button>
        <Button variant="outline" href={tag(release.calver)} rel="external"
          >{m.downloads_github()}</Button
        >
        <Button variant="ghost" href="{path}.md" data-sveltekit-reload
          >{m.downloads_markdown()}</Button
        >
      </div>
      {#if !release.isLatest}
        <p class="border-s-[3px] border-marker ps-3 text-sm text-pretty">
          {m.downloads_not_latest({ version: release.latest })}
          <a
            href="/downloads/{release.latest}"
            class="font-semibold underline underline-offset-4"
            >{m.downloads_not_latest_link({ version: release.latest })}</a
          >
        </p>
      {/if}
    {/if}
  </header>

  {#if release == null}
    <section class="grid gap-3 max-inline-[62ch]" aria-labelledby="empty">
      <h2 id="empty" class="heading">{m.downloads_empty_title()}</h2>
      <p class="text-[0.9375rem] text-pretty text-muted-foreground">
        {m.downloads_empty()}
      </p>
      <p class="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <a
          href="/api/v1/openapi.json"
          class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          >{m.footer_api()}</a
        >
        <a
          href="/agents"
          class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          >{m.agents_title()}</a
        >
      </p>
    </section>
  {:else}
    <div class="grid gap-x-12 gap-y-10 lg:grid-cols-[10rem_minmax(0,1fr)]">
      <nav
        aria-label={m.downloads_contents_nav()}
        class="lg:sticky lg:inset-bs-6 lg:self-start max-lg:hidden"
      >
        <ol class="grid gap-1 text-[0.8125rem]">
          {#each sections as section (section.id)}
            <li>
              <a
                href="#{section.id}"
                class="text-muted-foreground hover:text-foreground hover:underline hover:underline-offset-4"
                >{section.label}</a
              >
            </li>
          {/each}
        </ol>
      </nav>

      <div class="grid gap-14 min-inline-0">
        {#if lead != null}
          <section
            id="contents"
            class="grid scroll-mbs-6 gap-4"
            aria-labelledby="contents-title"
          >
            <div class="flex flex-wrap items-baseline justify-between gap-x-6">
              <h2 id="contents-title" class="heading">
                {m.downloads_contents()}
              </h2>
              {#if previous != null}
                <p
                  aria-hidden="true"
                  class="text-[0.75rem] text-muted-foreground"
                >
                  <span class="font-figures">+/−</span>
                  {m.downloads_delta_title({ version: previous.calver })}
                </p>
              {/if}
            </div>
            <div
              class="grid gap-x-10 gap-y-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
            >
              <div class="grid content-start gap-1">
                <p
                  class="font-figures text-[clamp(2.5rem,1.6rem+3vw,4rem)] leading-none font-[780] tracking-[-0.03em] tabular-nums"
                >
                  {count(lead.value)}
                </p>
                <p class="text-[0.9375rem]">
                  {m.downloads_lead_label()}
                  {#if summary?.span.first != null && summary.span.last != null}
                    <span class="text-muted-foreground"
                      >{m.downloads_lead_span({
                        first: summary.span.first.slice(0, 4),
                        last: summary.span.last.slice(0, 4),
                      })}</span
                    >
                  {/if}
                </p>
                {@render delta(lead.delta)}
              </div>
              <dl class="grid border-bs text-sm sm:grid-cols-2 sm:gap-x-8">
                {#each rest as total (total.key)}
                  <div
                    class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be py-1.5"
                  >
                    <dt>{totalLabel(total.key)}</dt>
                    <dd class="grid justify-items-end">
                      <span class="font-figures font-semibold tabular-nums"
                        >{count(total.value)}</span
                      >
                      {@render delta(total.delta)}
                    </dd>
                  </div>
                {/each}
              </dl>
            </div>
          </section>
        {/if}

        {#if summary != null && summary.countries.length > 0}
          <section
            id="countries"
            class="grid scroll-mbs-6 gap-4"
            aria-labelledby="countries-title"
          >
            <h2 id="countries-title" class="heading">
              {m.downloads_countries()}
            </h2>
            <Countries countries={summary.countries} {end} />
          </section>
        {/if}

        {#if breakdowns.length > 0}
          <section
            id="breakdown"
            class="grid scroll-mbs-6 gap-4"
            aria-labelledby="breakdown-title"
          >
            <h2 id="breakdown-title" class="heading">
              {m.downloads_breakdown()}
            </h2>
            <div class="grid gap-x-10 gap-y-8 sm:grid-cols-2 xl:grid-cols-3">
              {#each breakdowns as panel (panel.key)}
                {const rows = $derived(top(panel.rows))}
                {const most = $derived(
                  Math.max(1, ...rows.map((row) => row.value)),
                )}
                <div class="grid content-start gap-2">
                  <h3 class="text-[0.8125rem] font-semibold">{panel.title}</h3>
                  <ul class="grid border-bs text-[0.8125rem]">
                    {#each rows as row (row.key)}
                      <li
                        class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-be py-1.5"
                      >
                        <span
                          class="truncate"
                          class:text-muted-foreground={row.key === "__other"}
                          >{row.label}</span
                        >
                        <span class="font-figures tabular-nums"
                          >{count(row.value)}</span
                        >
                        <span
                          aria-hidden="true"
                          class="col-span-full rounded-e-[1px] block-1"
                          class:bg-foreground={row.key !== "__other"}
                          class:bg-muted-foreground={row.key === "__other"}
                          style:inline-size="{Math.max(
                            0.5,
                            (row.value / most) * 100,
                          )}%"
                        ></span>
                      </li>
                    {/each}
                  </ul>
                </div>
              {/each}
            </div>
          </section>
        {/if}

        {#if shapes.length > 0}
          <section
            id="shapes"
            class="grid scroll-mbs-6 gap-4"
            aria-labelledby="shapes-title"
          >
            <h2 id="shapes-title" class="heading">{m.downloads_shapes()}</h2>
            <p
              class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
            >
              {m.downloads_shapes_lede()}
            </p>
            <div class="overflow-x-auto">
              <table class="text-sm inline-full min-inline-lg">
                <thead>
                  <tr
                    class="border-be-2 border-foreground text-[0.75rem] text-muted-foreground"
                  >
                    <th scope="col" class="pbe-1.5 text-start font-semibold"
                      >{m.downloads_col_boundaries()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_col_shapes()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_col_areas()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_col_years()}</th
                    >
                  </tr>
                </thead>
                {#each shapes as group (group.code)}
                  <tbody>
                    <tr>
                      <th
                        scope="rowgroup"
                        colspan="4"
                        class="pbs-3 pbe-1 text-start font-semibold"
                        >{group.label}</th
                      >
                    </tr>
                    {#each group.rows as shape, index (`${shape.scheme}|${index}`)}
                      <tr class="border-be">
                        <th
                          scope="row"
                          class="py-1.5 ps-3 text-start font-normal"
                        >
                          {shape.noun ?? shape.scheme}
                          <span
                            class="ms-1.5 font-figures text-[0.75rem] text-muted-foreground"
                            >{shape.scheme}</span
                          >
                        </th>
                        <td class="py-1.5 text-end font-figures tabular-nums"
                          >{count(shape.shapes)}</td
                        >
                        <td
                          class="py-1.5 text-end font-figures text-muted-foreground tabular-nums"
                          >{count(shape.areas)}</td
                        >
                        <td
                          class="py-1.5 text-end font-figures text-muted-foreground tabular-nums"
                          >{span(shape.first, shape.last)}</td
                        >
                      </tr>
                    {/each}
                  </tbody>
                {/each}
              </table>
            </div>
          </section>
        {/if}

        <section
          id="changes"
          class="grid scroll-mbs-6 gap-4"
          aria-labelledby="changes-title"
        >
          <h2 id="changes-title" class="heading">{m.downloads_changes()}</h2>
          <p
            class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
          >
            {release.since == null
              ? m.downloads_changes_first({
                  count: release.changes,
                  until: date(release.upto.slice(0, 10)),
                })
              : m.downloads_changes_window({
                  count: release.changes,
                  since: date(release.since.slice(0, 10)),
                  until: date(release.upto.slice(0, 10)),
                })}
          </p>
          {#key release.calver}
            <Changes
              calver={release.calver}
              groups={release.groups}
              initial={release.page}
            />
          {/key}
        </section>

        <section
          id="files"
          class="grid scroll-mbs-6 gap-4"
          aria-labelledby="files-title"
        >
          <h2 id="files-title" class="heading">{m.downloads_files()}</h2>
          <p
            class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
          >
            {m.downloads_files_lede()}
          </p>
          <Files calver={release.calver} files={release.files} />
        </section>

        <section
          id="quickstart"
          class="grid scroll-mbs-6 gap-4"
          aria-labelledby="quickstart-title"
        >
          <h2 id="quickstart-title" class="heading">
            {m.downloads_quickstart()}
          </h2>
          <p
            class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
          >
            {m.downloads_quickstart_lede()}
          </p>
          <ol class="grid gap-5">
            {#each steps as step, index (step.code)}
              <li class="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-2">
                <span
                  class="font-figures text-[0.9375rem] font-semibold tabular-nums"
                  aria-hidden="true">{index + 1}</span
                >
                <p class="text-[0.9375rem] text-pretty">{step.text}</p>
                <div class="col-start-2">
                  {@render code(step.code, step.text)}
                </div>
              </li>
            {/each}
          </ol>
          <div class="grid gap-2 pbs-2">
            <h3 class="text-[0.9375rem] font-semibold">
              {m.downloads_one_file()}
            </h3>
            {@render code(
              `curl -LO ${latestUrl(origin, sample)}`,
              m.downloads_one_file(),
            )}
          </div>
          <div class="grid gap-2 pbs-2">
            <h3 class="text-[0.9375rem] font-semibold">
              {m.downloads_stable()}
            </h3>
            <p
              class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
            >
              {m.downloads_stable_lede()}
            </p>
            <ul class="grid border-bs text-sm">
              {#each SHARED_FILES as file (file)}
                <li
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-be py-1"
                >
                  <span class="grid min-inline-0">
                    <span class="truncate font-figures"
                      >{latestUrl(origin, file)}</span
                    >
                    <span class="text-[0.75rem] text-muted-foreground"
                      >{sharedLabel(file)}</span
                    >
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="{m.downloads_copy()}: {file}"
                    onclick={() => copyText(latestUrl(origin, file))}
                    ><CopyIcon /></Button
                  >
                </li>
              {/each}
            </ul>
          </div>
        </section>

        <section
          id="licenses"
          class="grid scroll-mbs-6 gap-4"
          aria-labelledby="licenses-title"
        >
          <h2 id="licenses-title" class="heading">
            {m.downloads_licenses()}
          </h2>
          <p
            class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
          >
            {m.downloads_licenses_lede()}
          </p>
          <dl class="grid border-bs text-sm">
            {#each bundles?.bundles ?? [] as name (name)}
              {const bundle = $derived(release.bundles[name])}
              {const size = $derived(bundles?.cell(name, "all"))}
              <div
                class="grid grid-cols-[6rem_minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-be py-2.5 max-sm:grid-cols-[6rem_minmax(0,1fr)]"
              >
                <dt class="font-figures font-semibold">{name}</dt>
                <dd class="grid gap-0.5">
                  {#if bundle != null}
                    <a
                      href={bundle.license_url}
                      rel="external license"
                      class="justify-self-start font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                      >{bundle.license}</a
                    >
                    <span class="text-pretty text-muted-foreground"
                      >{bundle.scope}</span
                    >
                  {/if}
                </dd>
                {#if size != null}
                  <dd
                    class="text-end font-figures text-muted-foreground tabular-nums max-sm:col-start-2 max-sm:text-start"
                  >
                    {bytes(size.bytes)}
                    {m.downloads_file_count({ count: size.files })}
                  </dd>
                {/if}
              </div>
            {/each}
          </dl>
          {#if release.held.length > 0}
            <div class="grid gap-2 pbs-2">
              <h3 class="text-[0.9375rem] font-semibold">
                {m.downloads_held()}
              </h3>
              <p
                class="text-sm text-pretty text-muted-foreground max-inline-[62ch]"
              >
                {m.downloads_held_lede()}
              </p>
              <ul class="grid list-disc gap-1 ps-5 text-sm">
                {#each release.held as entry (entry)}
                  <li class="text-pretty">{entry}</li>
                {/each}
              </ul>
            </div>
          {/if}
        </section>

        <section
          id="cite"
          class="grid scroll-mbs-6 gap-4 max-inline-176"
          aria-labelledby="cite-title"
        >
          <h2 id="cite-title" class="heading">{m.downloads_cite()}</h2>
          <CiteBody
            published={release.upto}
            release={{ calver: release.calver, doi: release.doi }}
          />
        </section>

        {#if releases.length > 1}
          <section
            id="releases"
            class="grid scroll-mbs-6 gap-4"
            aria-labelledby="releases-title"
          >
            <h2 id="releases-title" class="heading">
              {m.downloads_releases()}
            </h2>
            <div class="overflow-x-auto">
              <table class="text-sm inline-full min-inline-136">
                <thead>
                  <tr
                    class="border-be-2 border-foreground text-[0.75rem] text-muted-foreground"
                  >
                    <th scope="col" class="pbe-1.5 text-start font-semibold"
                      >{m.downloads_version()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-start font-semibold"
                      >{m.downloads_published_label()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_col_results()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_col_changes()}</th
                    >
                    <th scope="col" class="pbe-1.5 text-end font-semibold"
                      >{m.downloads_size_label()}</th
                    >
                  </tr>
                </thead>
                <tbody>
                  {#each releases as row, index (row.calver)}
                    <tr
                      class="border-be"
                      aria-current={row.calver === release.calver
                        ? "page"
                        : undefined}
                    >
                      <th scope="row" class="py-1.5 text-start">
                        {#if row.calver === release.calver}
                          <span class="font-figures font-semibold"
                            >{row.calver}</span
                          >
                        {:else}
                          <a
                            href="/downloads/{row.calver}"
                            class="font-figures font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
                            >{row.calver}</a
                          >
                        {/if}
                        {#if index === 0}
                          <span
                            class="ms-2 text-[0.75rem] font-normal text-muted-foreground"
                            >{m.downloads_latest_badge()}</span
                          >
                        {/if}
                      </th>
                      <td class="py-1.5 font-figures tabular-nums"
                        >{date(row.created_at.slice(0, 10))}</td
                      >
                      <td class="py-1.5 text-end font-figures tabular-nums"
                        >{row.totals?.results == null
                          ? "–"
                          : count(row.totals.results)}</td
                      >
                      <td class="py-1.5 text-end font-figures tabular-nums"
                        >{count(row.changes)}</td
                      >
                      <td class="py-1.5 text-end font-figures tabular-nums"
                        >{bytes(row.bytes)}</td
                      >
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </section>
        {/if}
      </div>
    </div>
  {/if}
</main>
