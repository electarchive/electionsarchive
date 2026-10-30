<script lang="ts">
  import { cn } from "#lib/utils.js";
  import type { Column, Proposal } from "./data.js";
  import { copy } from "./copy.js";
  import { columns, counties, integer, page, surnames } from "./data.js";
  import Proof from "#lib/components/contrib/proof.svelte";
  import Scan from "./scan.svelte";

  const {
    proposal,
    compact = false,
    since = false,
  }: { proposal: Proposal; compact?: boolean; since?: boolean } = $props();

  const region = [0.02, 0.435, 0.98, 0.745] as const;

  let open = $state<string | null>(null);

  const desk = $derived(
    {
      approve: copy.desk_approve,
      changes: copy.desk_changes,
      escalated: copy.desk_escalated,
      pending: copy.desk_pending,
    }[proposal.desk],
  );
</script>

<div class={cn("grid", { "gap-6": !compact, "gap-5": compact })}>
  <p
    class={cn("font-serif leading-relaxed text-pretty max-inline-[60ch]", {
      "text-lg": !compact,
    })}
  >
    {proposal.sentence}
  </p>

  <dl
    class="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-4 border-bs text-sm max-sm:grid-cols-1"
  >
    <dt
      class="border-be py-2 text-muted-foreground max-sm:border-be-0 max-sm:pbe-0"
    >
      {copy.desk_label}
    </dt>
    <dd class="border-be py-2">
      <span class="font-semibold">{desk}.</span>
      {proposal.deskNote}
    </dd>
    <dt
      class="border-be py-2 text-muted-foreground max-sm:border-be-0 max-sm:pbe-0"
    >
      {copy.checks_label}
    </dt>
    <dd class="border-be py-2">
      {copy.check_line(
        proposal.checks.passed,
        proposal.checks.findings,
        proposal.checks.failed,
      )}
    </dd>
    {#if proposal.touches != null}
      <dt
        class="border-be py-2 text-muted-foreground max-sm:border-be-0 max-sm:pbe-0"
      >
        {copy.touches_label}
      </dt>
      <dd class="border-be py-2">{proposal.touches}</dd>
    {/if}
  </dl>

  <section aria-labelledby="changes-{proposal.number}">
    <h2
      id="changes-{proposal.number}"
      class={cn({ heading: !compact, "font-bold": compact })}
    >
      {since ? copy.since_title : copy.changes_title}
    </h2>
    {#if proposal.table}
      <p class="mbs-1 text-sm text-muted-foreground">
        {since ? copy.since_summary : copy.table_summary}
      </p>
      <table class="mbs-3 text-sm inline-full">
        <thead class="text-muted-foreground">
          <tr class="border-be">
            <th class="py-1.5 ps-3 text-start font-normal">{copy.county}</th>
            {#each surnames as name (name)}
              <th class="py-1.5 text-end font-normal">{name}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each since ? counties.filter((county) => county.id === "kankakee") : counties as county (county.id)}
            <tr
              class={cn("border-be", {
                "bg-muted": open?.startsWith(county.id) === true,
              })}
            >
              <td class="border-s-2 border-foreground py-1.5 ps-3">
                {county.name}{#if county.isPartial}<span
                    class="ms-1 text-muted-foreground">{copy.part}</span
                  >{/if}
              </td>
              {#each columns as column (column)}
                {const id = $derived(`${county.id}-${column}`)}
                <td class="py-1.5 text-end">
                  {#if since && column === 0}
                    <Proof
                      before="26,593"
                      after={integer.format(county.votes[0])}
                      n={1}
                      label={copy.open_crop(county.name)}
                      onopen={() => (open = open === id ? null : id)}
                    />
                  {:else}
                    <Proof
                      before={null}
                      after={integer.format(county.votes[column])}
                      n={1}
                      label={copy.open_crop(county.name)}
                      onopen={() => (open = open === id ? null : id)}
                    />
                  {/if}
                </td>
              {/each}
            </tr>
            {#if open?.startsWith(county.id) === true}
              {const column = $derived<Column>(open.endsWith("-1") ? 1 : 0)}
              <tr class="border-be">
                <td colspan="3" class="py-2 ps-3">
                  <Scan
                    region={[
                      0.02,
                      county.box[1] - 0.016,
                      0.62,
                      county.box[3] + 0.016,
                    ]}
                    marks={[
                      {
                        id: "cell",
                        box: county.boxes[column],
                        label: county.name,
                      },
                    ]}
                    selected="cell"
                    alt={copy.crop_alt(county.name)}
                    class="rounded-[2px] border max-inline-md"
                  />
                  <p class="mbs-1 text-xs text-muted-foreground">
                    {copy.crop_caption(page.number)}
                  </p>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {:else}
      <ul class="mbs-3 grid text-sm">
        {#each proposal.changes as change (change.entity + change.field)}
          <li
            class={cn(
              "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 border-be py-1.5 ps-3",
              { "border-s-2 border-foreground": change.before == null },
            )}
          >
            <span class="min-inline-0">
              <span class="block truncate">{change.entity}</span>
              <span class="text-xs text-muted-foreground">{change.field}</span>
            </span>
            <Proof
              before={change.before}
              after={change.after}
              n={change.evidence}
              class="text-end"
            />
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section aria-labelledby="evidence-{proposal.number}">
    <h2
      id="evidence-{proposal.number}"
      class={cn({ heading: !compact, "font-bold": compact })}
    >
      {copy.evidence_title}
    </h2>
    {#if proposal.evidence.length === 0}
      <p class="mbs-2 text-sm font-semibold">{copy.no_evidence}</p>
    {:else}
      <ol class="mbs-2 grid gap-1 text-sm">
        {#each proposal.evidence as item, i (item.label)}
          <li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-1">
            <span class="font-figures text-muted-foreground">{i + 1}</span>
            <span>
              <span class="block">{item.label}</span>
              <span class="text-xs text-muted-foreground">{item.detail}</span>
            </span>
          </li>
        {/each}
      </ol>
      {#if proposal.table && !compact}
        <Scan
          region={[...region]}
          marks={counties.flatMap((county) =>
            county.boxes.map((box, column) => ({
              id: `${county.id}-${column}`,
              box,
              label: county.name,
            })),
          )}
          selected={open}
          alt={copy.ws_scan_alt}
          class="mbs-4 rounded-[2px] border"
        />
      {/if}
    {/if}
  </section>
</div>
