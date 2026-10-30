<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import type { DiffRow } from "#lib/contrib.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import ScanCrop from "#lib/components/contrib/scan-crop.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import {
    channelLabel,
    dated,
    escalationLabel,
    factLabel,
    measureLabel,
    noteKindLabel,
    originLabel,
    origins,
    precisions,
    recommendationLabel,
    schemeLabel,
    schemes,
    stateLabel,
    tierLabel,
    verdictLabel,
  } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const proposal = $derived(data.proposal);
  const head = $derived(proposal.revisions.at(0));
  const job = $derived(proposal.jobs.at(0));
  const pending = $derived(
    job?.status === "queued" || job?.status === "running",
  );
  const working: Record<string, () => string> = {
    publish: m.proposal_publishing,
    reject: m.proposal_rejecting,
    revert: m.proposal_reverting,
  };
  const evidential = ["citation", "source", "source_artifact", "source_page"];
  const cites = (locator: unknown) =>
    typeof locator === "object" &&
    locator != null &&
    "op" in locator &&
    locator.op === "cite";
  const fold = $derived(
    proposal.state === "approved" && head?.diff?.fold != null
      ? head.diff.fold
      : null,
  );
  const cells = $derived(
    (head?.diff?.rows ?? []).filter((row) => row.table === "result_value"),
  );
  let opened = $state<number | null>(null);
  const rows = $derived(
    (head?.diff?.rows ?? [])
      .filter(
        (row) =>
          row.table !== "result_value" &&
          (!evidential.includes(row.table) ||
            (row.table === "citation" &&
              (row.op === "drop" || cites(row.fields.locator?.[1])))),
      )
      .map((row): DiffRow =>
        row.table === "external_id" && row.op === "add"
          ? {
              ...row,
              fields: {
                scheme: [null, row.key.scheme],
                value: [null, row.key.value],
                ...row.fields,
              },
            }
          : row,
      ),
  );
  const titles = $derived.by(() => {
    const found: Record<string, string> = { ...head?.titles };
    for (const row of head?.diff?.rows ?? []) {
      const title = row.fields.title?.[1];
      if (row.table === "source" && typeof title === "string") {
        found[row.key.id ?? ""] = title;
      }
    }
    return found;
  });
  const findings = $derived.by(() => {
    let total = 0;
    for (const value of Object.values(head?.checks?.findings ?? {})) {
      total += value;
    }
    return total;
  });
  const tables: Record<string, () => string> = {
    name: m.proposal_table_name,
    citation: m.proposal_table_citation,
    note: m.proposal_table_note,
    translation: m.proposal_table_translation,
    external_id: m.proposal_table_external_id,
    party_color: m.proposal_table_party_color,
    party_image: m.proposal_table_party_image,
    person_image: m.proposal_table_person_image,
    result_set: m.proposal_table_result_set,
    outcome: m.proposal_table_outcome,
  };
  const fields: Record<string, () => string> = {
    source_id: m.proposal_field_source,
    kind: m.proposal_field_kind,
    lang: m.proposal_field_lang,
    origin: m.proposal_field_origin,
    source_lang: m.proposal_field_source_lang,
    color: m.proposal_field_color,
    valid_from: m.proposal_field_valid_from,
    valid_to: m.proposal_field_valid_to,
    license: m.proposal_field_license,
    artist: m.proposal_field_artist,
    source_url: m.proposal_field_source_url,
    taken_text: m.proposal_field_taken_text,
    stage: m.proposal_field_stage,
    is_default: m.proposal_field_is_default,
    status: m.proposal_field_status,
    as_of: m.proposal_field_as_of,
    seats: m.proposal_field_seats,
  };
  const hidden = new Set([
    "id",
    "entity_id",
    "locator",
    "name_id",
    "note_id",
    "scheme",
    "source_md5",
    "stale_from",
    "valid_from_precision",
    "valid_to_precision",
    "party_id",
    "person_id",
    "mark_path",
    "width",
    "height",
    "shape",
    "is_plate",
    "is_light",
    "dated_by",
    "dated_url",
    "uploaded_at",
    "taken_from",
    "taken_from_precision",
    "taken_to",
    "taken_to_precision",
    "result_set_id",
    "option_id",
    "election_id",
    "contest_id",
    "supersedes_id",
    "completeness",
  ]);
  const unlabeled = new Set(["text", "body_md", "body", "path"]);
  const order = [
    "path",
    "text",
    "body_md",
    "body",
    "kind",
    "lang",
    "source_lang",
    "origin",
    "value",
    "color",
    "valid_from",
    "valid_to",
    "license",
    "artist",
    "taken_text",
    "source_url",
    "source_id",
  ];
  const rank = (field: string) => {
    const at = order.indexOf(field);
    return at === -1 ? order.length : at;
  };
  const languageName = new Intl.DisplayNames([getLocale()], {
    type: "language",
  });
  const noteKind = (value: string) =>
    value === "context" ||
    value === "caveat" ||
    value === "methodology" ||
    value === "editorial"
      ? noteKindLabel[value]()
      : value;
  const scheme = (fields: Record<string, [unknown, unknown]>) => {
    const value = fields.scheme?.[1] ?? fields.scheme?.[0];
    const found = schemes.find((item) => item === value);
    return found == null ? String(value) : schemeLabel[found]();
  };
  const checks: Record<string, (pipelines: string) => string> = {
    "scope.owner": (pipelines) => m.proposal_check_scope_owner({ pipelines }),
    "scope.polity": m.proposal_check_scope_polity,
    "name.capitals": m.proposal_check_name_capitals,
    "translation.held": m.proposal_check_translation_held,
    "facts.immutable": m.proposal_check_facts_immutable,
    "reference.facts": m.proposal_check_reference_facts,
  };
  const found = $derived(
    Object.entries(head?.checks?.findings ?? {})
      .filter(([, value]) => value > 0)
      .map(([code, value]) => {
        const pipelines: Array<string> = [];
        for (const example of head?.checks?.examples?.[code] ?? []) {
          const pipeline = example.details?.pipeline;
          if (pipeline != null && !pipelines.includes(pipeline)) {
            pipelines.push(pipeline);
          }
        }
        return {
          code,
          value,
          label: checks[code]?.(pipelines.join(", ")) ?? code,
        };
      }),
  );
  const day = (value: string | null) => date(value?.slice(0, 10) ?? null);
  const figure = (value: unknown) =>
    typeof value === "number" ? count(value) : null;
  const precision = (value: unknown) =>
    precisions.find((item) => item === value) ?? "day";
  const shown = (field: string, value: unknown, at: unknown) => {
    if (value == null) {
      return null;
    }
    if (typeof value !== "string") {
      return JSON.stringify(value);
    }
    switch (field) {
      case "source_id": {
        return titles[value] ?? value;
      }
      case "kind": {
        return noteKind(value);
      }
      case "lang":
      case "source_lang": {
        return languageName.of(value) ?? value;
      }
      case "origin": {
        const found = origins.find((item) => item === value);
        return found == null ? value : originLabel[found]();
      }
      case "valid_from":
      case "valid_to": {
        return dated(value, precision(at));
      }
      default: {
        return value;
      }
    }
  };
  const decided = $derived(
    proposal.state === "published"
      ? m.proposal_published_by
      : proposal.state === "rejected"
        ? m.proposal_rejected_by
        : proposal.state === "reverted"
          ? m.proposal_reverted_by
          : proposal.state === "changes_requested"
            ? m.proposal_changes_requested_by
            : null,
  );

  let reason = $state("");
  let busy = $state(false);

  $effect(() => {
    if (!pending && (proposal.state !== "approved" || fold != null)) {
      return;
    }
    const timer = setInterval(() => void invalidateAll(), 2500);
    return () => {
      clearInterval(timer);
    };
  });
</script>

<svelte:head>
  <title>{m.proposal_title({ number: proposal.number })}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<div class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <p class="flex flex-wrap items-baseline gap-x-4 text-sm">
      <span class="font-figures font-semibold">#{proposal.number}</span>
      <span class={cn({ "font-semibold": proposal.state !== "open" })}
        >{fold == null
          ? stateLabel[proposal.state]()
          : fold.mismatch == null
            ? m.proposal_state_awaiting_fold()
            : m.proposal_state_fold_mismatch()}</span
      >
      {#if proposal.tier != null}
        <span class="text-muted-foreground">{tierLabel[proposal.tier]?.()}</span
        >
      {/if}
    </p>
    <h1 class="title text-balance">{proposal.title}</h1>
    <p class="text-sm text-muted-foreground">
      {m.proposal_by({
        author: proposal.author ?? m.proposal_unknown_author(),
        date: day(proposal.created_at),
      })}
    </p>
  </header>

  <dl
    class="mbs-6 grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-4 border-y text-sm max-sm:grid-cols-1"
  >
    <dt class="py-2 text-muted-foreground max-sm:pbe-0">
      {decided == null ? m.proposal_checks() : m.proposal_decision()}
    </dt>
    <dd class="py-2" role="status">
      {#if pending}
        {(working[job?.kind ?? ""] ?? m.proposal_checking)()}
      {:else if job?.status === "failed"}
        {m.proposal_failed({ reason: job.error ?? job.kind })}
      {:else if decided != null}
        <span class="font-semibold"
          >{decided({
            name: proposal.decider ?? m.proposal_unknown_author(),
            date: day(proposal.decided_at),
          })}</span
        >
        {#if proposal.decision_reason != null}
          <span class="block text-pretty">{proposal.decision_reason}</span>
        {/if}
      {:else if fold != null}
        {const pipeline = $derived(
          fold.sets.map((item) => item.pipeline).join(", "),
        )}
        <span class="block text-pretty"
          >{fold.mismatch == null
            ? m.proposal_fold_waiting({ pipeline })
            : m.proposal_fold_mismatch({
                pipeline,
                count: fold.mismatch.cells,
              })}</span
        >
      {:else if head?.checks?.unchanged === true}
        {m.proposal_unchanged()}
      {:else if findings === 0}
        {m.proposal_checks_passed()}
      {:else}
        {m.proposal_findings({ count: findings })}
        <ul class="mbs-1 grid gap-0.5 text-muted-foreground">
          {#each found as item (item.code)}
            <li class="flex items-baseline gap-2">
              <span class="text-pretty">{item.label}</span>
              {#if item.value > 1}
                <span class="font-figures">{count(item.value)}</span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </dd>
    {#if proposal.prescreen != null || proposal.state === "open"}
      <div class="col-span-full grid grid-cols-subgrid border-bs">
        <dt class="py-2 text-muted-foreground max-sm:pbe-0">
          {m.proposal_prescreen()}
        </dt>
        <dd class="py-2">
          {#if proposal.prescreen == null}
            <span class="text-muted-foreground"
              >{m.proposal_prescreen_waiting()}</span
            >
          {:else}
            <span
              class={cn({
                "font-semibold": proposal.prescreen.verdict !== "clean",
              })}>{verdictLabel[proposal.prescreen.verdict]()}</span
            >
            {#if proposal.prescreen.reason != null}
              <span class="block text-pretty text-muted-foreground"
                >{proposal.prescreen.reason}</span
              >
            {/if}
          {/if}
        </dd>
      </div>
    {/if}
    {#if proposal.escalations.length > 0 && proposal.state === "open"}
      <div class="col-span-full grid grid-cols-subgrid border-bs">
        <dt class="py-2 text-muted-foreground max-sm:pbe-0">
          {m.proposal_escalations()}
        </dt>
        <dd class="py-2">
          <ul class="grid gap-0.5">
            {#each proposal.escalations as code (code)}
              <li>{escalationLabel[code]()}</li>
            {/each}
          </ul>
        </dd>
      </div>
    {/if}
  </dl>

  <section class="mbs-8" aria-labelledby="changes">
    <h2 id="changes" class="heading">{m.proposal_changes()}</h2>
    <ul class="mbs-2 grid border-bs text-sm">
      {#each rows as row, i (i)}
        <li
          class="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-4 border-be py-2 max-sm:grid-cols-1"
        >
          <span class="text-muted-foreground"
            >{tables[row.table]?.() ?? row.table}{#if row.op === "drop"}<span
                class="block text-xs font-semibold text-foreground"
                >{m.proposal_removed()}</span
              >{/if}{#if row.table === "party_color" && row.op !== "add"}<span
                class="block text-xs"
                >{row.key.valid_from == null
                  ? m.proposal_color_start()
                  : m.proposal_color_from({
                      date: dated(
                        row.key.valid_from,
                        precision(row.key.valid_from_precision),
                      ),
                    })}</span
              >{/if}{#if row.table === "translation"}<span class="block text-xs"
                >{m.proposal_translation_into({
                  language:
                    languageName.of(row.key.lang ?? "") ?? row.key.lang ?? "",
                })}</span
              >{#if row.fields.stale_from?.[1] != null}<span
                  class="block text-xs font-semibold text-foreground"
                  >{m.proposal_translation_outdated()}</span
                >{/if}{/if}</span
          >
          <span class="grid gap-1">
            {#if row.table === "translation"}
              {const original = $derived(
                head?.originals?.[row.key.source_md5 ?? ""],
              )}
              {#if original != null}
                <span class="grid gap-0.5 border-s-2 border-border ps-2">
                  <span class="text-xs text-muted-foreground"
                    >{m.proposal_translation_original()}</span
                  >
                  <span
                    class="text-pretty whitespace-pre-line text-muted-foreground"
                    lang={original.lang}
                    dir="auto">{original.body}</span
                  >
                </span>
              {/if}
            {/if}
            {#each Object.entries(row.fields)
              .filter(([field, [before, after]]) => !hidden.has(field) && (before != null || after != null))
              .toSorted(([a], [b]) => rank(a) - rank(b)) as [field, [before, after]] (field)}
              <span class="flex flex-wrap items-baseline gap-x-2">
                {#if row.table === "external_id" && field === "value"}
                  <span class="text-xs text-muted-foreground"
                    >{scheme(row.fields)}</span
                  >
                {:else if !unlabeled.has(field)}
                  <span class="text-xs text-muted-foreground"
                    >{fields[field]?.() ?? field}</span
                  >
                {/if}
                {#if field === "path" && typeof after === "string"}
                  <img
                    src="/{after}"
                    alt=""
                    class="rounded-xs border border-border bg-white object-contain p-1 max-block-24 max-inline-full"
                  />
                {:else if field === "source_url" && typeof after === "string"}
                  <a
                    class="truncate underline-offset-2 min-inline-0 hover:underline"
                    href={after}
                    rel="external nofollow noopener noreferrer"
                    target="_blank">{after}</a
                  >
                {:else}
                  {#if field === "color"}
                    {const swatches = $derived(
                      [before, after].filter(
                        (value): value is string => typeof value === "string",
                      ),
                    )}
                    {#each swatches as swatch, index (index)}
                      <span
                        class="self-center rounded-xs border border-foreground/20 block-3.5 inline-3.5"
                        style:background-color={swatch}
                        aria-hidden="true"
                      ></span>
                    {/each}
                  {/if}
                  <Proof
                    class={cn("flex-wrap", {
                      "whitespace-pre-line": field === "body",
                    })}
                    lang={row.table === "translation" && field === "body"
                      ? row.key.lang
                      : undefined}
                    before={shown(
                      field,
                      before,
                      row.fields[`${field}_precision`]?.[0],
                    )}
                    after={shown(
                      field,
                      after,
                      row.fields[`${field}_precision`]?.[1] ??
                        row.fields[`${field}_precision`]?.[0],
                    )}
                  />
                {/if}
              </span>
            {/each}
          </span>
        </li>
      {/each}
    </ul>
    {#if cells.length > 0}
      <div class="mbs-4 flex items-baseline justify-between gap-4">
        <h3 class="text-sm font-semibold">{m.proposal_results()}</h3>
        <span class="text-xs text-muted-foreground"
          >{m.proposal_results_count({ count: cells.length })}</span
        >
      </div>
      <div class="mbs-1 overflow-x-auto">
        <table class="border-bs text-sm inline-full">
          <thead>
            <tr class="border-be text-xs text-muted-foreground">
              <th class="py-1.5 pe-4 text-start font-normal"
                >{m.proposal_results_unit()}</th
              >
              <th class="py-1.5 pe-4 text-start font-normal"
                >{m.proposal_results_option()}</th
              >
              <th class="py-1.5 pe-4 text-start font-normal"
                >{m.proposal_results_measure()}</th
              >
              <th class="py-1.5 text-end font-normal"
                >{m.proposal_results_value()}</th
              >
            </tr>
          </thead>
          <tbody>
            {#each cells as cell, i (i)}
              {const previous = $derived(cells[i - 1]?.labels?.unit)}
              {const page = $derived(
                cell.locator == null
                  ? null
                  : (head?.pages?.[cell.locator.page_id] ?? null),
              )}
              {const channel = $derived(cell.labels?.channel ?? "all")}
              <tr
                class={cn("align-baseline", {
                  "border-bs": previous !== cell.labels?.unit,
                })}
              >
                <td class="py-1.5 pe-4"
                  >{#if previous !== cell.labels?.unit}{cell.labels
                      ?.unit}{/if}</td
                >
                <td class="py-1.5 pe-4">{cell.labels?.option ?? ""}</td>
                <td class="py-1.5 pe-4 text-muted-foreground"
                  >{measureLabel(
                    cell.labels?.measure ?? "",
                  )}{#if channel !== "all"}<span class="block text-xs"
                      >{channelLabel(channel)}</span
                    >{/if}</td
                >
                <td class="py-1.5 text-end whitespace-nowrap">
                  <Proof
                    class="justify-end"
                    before={figure(cell.fields.value?.[0])}
                    after={figure(cell.fields.value?.[1])}
                    n={page == null ? undefined : i + 1}
                    label={page == null
                      ? undefined
                      : m.proposal_results_mark({ n: i + 1, page: page.page })}
                    onopen={page == null
                      ? undefined
                      : () => {
                          opened = opened === i ? null : i;
                        }}
                  />
                </td>
              </tr>
              {#if opened === i && page != null}
                {const href = $derived(
                  `/uploads/${page.upload}/pages/${page.page}`,
                )}
                <tr>
                  <td colspan="4" class="pbe-3">
                    <figure class="grid justify-items-end gap-1.5">
                      <ScanCrop
                        {href}
                        width={page.width}
                        height={page.height}
                        region={cell.locator?.region ?? null}
                        label={m.proposal_scan_alt({ page: page.page })}
                        limit={480}
                      />
                      <figcaption
                        class="flex flex-wrap items-baseline gap-x-3 text-xs text-muted-foreground"
                      >
                        <span class="font-figures"
                          >{m.proposal_scan_page({ page: page.page })}</span
                        >
                        <a
                          class="underline underline-offset-2 hover:text-foreground"
                          {href}
                          rel="external">{m.proposal_scan_open()}</a
                        >
                        <button
                          type="button"
                          class="underline underline-offset-2 hover:text-foreground"
                          onclick={() => {
                            opened = null;
                          }}>{m.proposal_results_hide()}</button
                        >
                      </figcaption>
                    </figure>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
    {#if head?.diff?.truncated === true}
      <p class="mbs-2 text-sm text-muted-foreground">
        {m.proposal_truncated()}
      </p>
    {/if}
  </section>

  {#if (head?.sources ?? []).length > 0}
    <section class="mbs-8" aria-labelledby="evidence">
      <h2 id="evidence" class="heading">{m.proposal_evidence()}</h2>
      <ul class="mbs-2 grid border-bs text-sm">
        {#each head?.sources ?? [] as item (item.key)}
          {const quote = $derived(head?.payload.evidence?.[item.key]?.quote)}
          <li class="grid gap-1 border-be py-2">
            <span class="font-semibold">{item.title}</span>
            {#if item.url != null}
              <span
                class="flex flex-wrap items-baseline gap-x-3 text-xs text-muted-foreground"
              >
                <a
                  class="truncate underline-offset-2 min-inline-0 hover:underline"
                  href={item.url}
                  rel="external nofollow noopener noreferrer"
                  target="_blank">{item.url}</a
                >
                {#if item.captured != null}
                  <span class="shrink-0"
                    >{m.proposal_archived()} · {day(item.captured)}</span
                  >
                {/if}
                {#if item.wayback != null}
                  <a
                    class="shrink-0 underline underline-offset-2 hover:text-foreground"
                    href={item.wayback}
                    rel="external nofollow noopener noreferrer"
                    target="_blank">{m.proposal_wayback()}</a
                  >
                {/if}
              </span>
            {/if}
            {#if item.scan != null}
              {const scan = $derived(item.scan)}
              {const href = $derived(
                `/uploads/${scan.upload}/pages/${scan.page}`,
              )}
              <figure class="grid gap-1.5 pbs-1">
                <ScanCrop
                  {href}
                  width={scan.width}
                  height={scan.height}
                  region={scan.region}
                  label={m.proposal_scan_alt({ page: scan.page })}
                />
                <figcaption
                  class="flex flex-wrap items-baseline gap-x-3 text-xs text-muted-foreground"
                >
                  <span class="font-figures"
                    >{m.proposal_scan_page({ page: scan.page })}</span
                  >
                  <a
                    class="underline underline-offset-2 hover:text-foreground"
                    {href}
                    rel="external">{m.proposal_scan_open()}</a
                  >
                </figcaption>
              </figure>
            {/if}
            {#if quote != null}
              <q
                class="border-s-2 border-border ps-2 text-pretty text-muted-foreground"
                >{quote}</q
              >
            {/if}
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if proposal.prereview != null}
    {const review = $derived(proposal.prereview)}
    <section class="mbs-8" aria-labelledby="prereview">
      <h2 id="prereview" class="heading">{m.proposal_prereview()}</h2>
      <p class="mbs-2 flex flex-wrap items-baseline gap-x-3 text-sm">
        <span class="font-semibold"
          >{recommendationLabel[review.recommendation]()}</span
        >
        <span class="text-muted-foreground"
          >{m.prereview_by({
            name: proposal.prereviewer ?? m.proposal_unknown_author(),
            date: day(review.at),
          })}</span
        >
      </p>
      {#if review.revision !== head?.n}
        <p class="mbs-1 text-sm text-muted-foreground">
          {m.prereview_stale({ revision: review.revision })}
        </p>
      {/if}
      <p class="mbs-3 text-sm text-pretty whitespace-pre-line">
        {review.summary}
      </p>
      {#if review.facts.length > 0}
        <table class="mbs-4 border-bs text-sm inline-full">
          <thead>
            <tr class="border-be text-muted-foreground">
              <th class="py-2 pe-4 text-start font-normal"
                >{m.prereview_fact()}</th
              >
              <th class="py-2 text-end font-normal">{m.prereview_status()}</th>
            </tr>
          </thead>
          <tbody>
            {#each review.facts as fact, index (index)}
              <tr class="border-be align-baseline">
                <td class="py-2 pe-4">
                  <span class="block text-pretty">{fact.fact}</span>
                  {#if fact.note != null}
                    <span
                      class="block text-xs text-pretty text-muted-foreground"
                      >{fact.note}</span
                    >
                  {/if}
                </td>
                <td
                  class={cn(
                    "py-2 text-end whitespace-nowrap",
                    {
                      "font-semibold text-destructive":
                        fact.status === "contradicted",
                    },
                    { "text-muted-foreground": fact.status === "unverified" },
                  )}>{factLabel[fact.status]()}</td
                >
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#if review.risks.length > 0}
        <h3 class="mbs-4 text-sm font-semibold">{m.prereview_risks()}</h3>
        <ul class="mbs-1 grid list-disc gap-1 ps-5 text-sm">
          {#each review.risks as risk, index (index)}
            <li class="text-pretty">{risk}</li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}

  {#if data.canDecide && !pending}
    <section class="mbs-10 grid gap-3" aria-labelledby="decide">
      <h2 id="decide" class="heading">{m.proposal_decision()}</h2>
      <form
        method="POST"
        class="grid gap-3"
        use:enhance={() => {
          busy = true;
          return async ({ update }) => {
            await update();
            busy = false;
          };
        }}
      >
        <Textarea
          name="reason"
          bind:value={reason}
          aria-label={m.proposal_decision_reason()}
          placeholder={m.proposal_decision_reason()}
          maxlength={2000}
          rows={3}
        />
        {#if form?.message != null}
          <p class="text-sm text-destructive" role="alert">{form.message}</p>
        {:else if !data.canApprove}
          <p class="text-sm text-muted-foreground">{m.proposal_admin_only()}</p>
        {/if}
        <div class="flex flex-wrap justify-end gap-2">
          {#if data.canReject}
            <Button
              type="submit"
              variant="outline"
              formaction="?/reject"
              disabled={busy || reason.trim() === ""}
              >{m.proposal_reject()}</Button
            >
          {/if}
          <Button
            type="submit"
            variant="outline"
            formaction="?/request_changes"
            disabled={busy || reason.trim() === ""}
            >{m.proposal_request_changes()}</Button
          >
          {#if data.canApprove}
            <Button type="submit" formaction="?/approve" disabled={busy}
              >{m.proposal_approve()}</Button
            >
          {/if}
        </div>
      </form>
    </section>
  {/if}
</div>
