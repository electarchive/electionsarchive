<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import type { DiffRow } from "#lib/contrib.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import ScanCrop from "#lib/components/contrib/scan-crop.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import {
    dated,
    noteKindLabel,
    originLabel,
    origins,
    precisions,
    schemeLabel,
    schemes,
    stateLabel,
    tierLabel,
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
  const rows = $derived(
    (head?.diff?.rows ?? [])
      .filter((row) => !evidential.includes(row.table))
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
    note: m.proposal_table_note,
    translation: m.proposal_table_translation,
    external_id: m.proposal_table_external_id,
    party_color: m.proposal_table_party_color,
    party_image: m.proposal_table_party_image,
    person_image: m.proposal_table_person_image,
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
  };
  const hidden = new Set([
    "entity_id",
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
          : null,
  );

  let reason = $state("");
  let busy = $state(false);

  $effect(() => {
    if (!pending && proposal.state !== "approved") {
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
        >{stateLabel[proposal.state]()}</span
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
          aria-label={m.proposal_reject_reason()}
          placeholder={m.proposal_reject_reason()}
          maxlength={2000}
          rows={3}
        />
        {#if form?.message != null}
          <p class="text-sm text-destructive" role="alert">{form.message}</p>
        {/if}
        <div class="flex flex-wrap justify-end gap-2">
          <Button
            type="submit"
            variant="outline"
            formaction="?/reject"
            disabled={busy || reason.trim() === ""}
            >{m.proposal_reject()}</Button
          >
          <Button type="submit" formaction="?/approve" disabled={busy}
            >{m.proposal_approve()}</Button
          >
        </div>
      </form>
    </section>
  {/if}
</div>
