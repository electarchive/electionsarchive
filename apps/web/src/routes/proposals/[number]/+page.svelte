<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import Proof from "#lib/components/contrib/proof.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Textarea } from "#lib/components/ui/textarea/index.js";
  import { stateLabel, tierLabel } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
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
  const rows = $derived(
    (head?.diff?.rows ?? []).filter((row) => row.table !== "citation"),
  );
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
  };
  const fields: Record<string, () => string> = {
    source_id: m.proposal_field_source,
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
  const shown = (field: string, value: unknown) =>
    value == null
      ? null
      : typeof value === "string"
        ? field === "source_id"
          ? (head?.titles?.[value] ?? value)
          : value
        : JSON.stringify(value);
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
            >{tables[row.table]?.() ?? row.table}</span
          >
          <span class="grid gap-1">
            {#each Object.entries(row.fields) as [field, [before, after]] (field)}
              <span class="flex flex-wrap items-baseline gap-x-2">
                {#if field !== "text"}
                  <span class="text-xs text-muted-foreground"
                    >{fields[field]?.() ?? field}</span
                  >
                {/if}
                <Proof
                  class="flex-wrap"
                  before={shown(field, before)}
                  after={shown(field, after)}
                />
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
