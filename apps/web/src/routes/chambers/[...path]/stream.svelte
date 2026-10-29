<script lang="ts">
  import Makeup from "#routes/[...path]/makeup.svelte";
  import { page } from "$app/state";
  import Export from "#lib/components/export.svelte";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    terms,
    parties,
    governments,
    interim,
    eras,
    basis,
    selected = $bindable(),
  }: {
    terms: Array<{
      start: string;
      end: string | null;
      total: number;
      size: number | null;
      apportioned: number | null;
      seceded: number;
      isSeated: boolean;
      isPartial: boolean;
      parties: Array<{ party: string | null; seats: number }>;
      election: { name: string; path: string | null; date: string } | null;
      title: string | null;
      names: Record<string, { label: string; short: string | null }>;
      steps: Array<{
        date: string;
        parties: Array<{ party: string | null; seats: number }>;
        moves: Array<{
          kind: "in" | "out" | "switch" | "rename";
          from: string | null;
          to: string | null;
          cause: string | null;
          members: number;
        }>;
      }>;
    }>;
    parties: Array<{
      id: string;
      label: string;
      short: string | null;
      color: string | null;
      lean: number | null;
      isStriped: boolean;
      isMade: boolean;
    }>;
    governments: Array<{
      name: string;
      head: { id: string; name: string } | null;
      party: string | null;
      from: string;
      to: string | null;
      isCaretaker: boolean;
    }>;
    interim: Array<{ name: string; from: string | null; to: string | null }>;
    eras: Array<{
      party: string;
      from: string;
      label: string;
      short: string | null;
    }>;
    basis: "seated" | "mixed" | "results";
    selected: number;
  } = $props();

  const uid = $props.id();

  let isShare = $state(page.url.searchParams.get("scale") === "share");

  $effect(() => {
    keep({ scale: isShare ? "share" : null });
  });
</script>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-labelledby="{uid}-title"
  data-export
>
  <Export name="makeup" class="absolute inset-e-2 inset-bs-2" />
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">
        {m.chamber_makeup_title()}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[68ch]">
        {basis === "seated"
          ? m.chamber_makeup_seated()
          : basis === "mixed"
            ? m.chamber_makeup_mixed()
            : m.chamber_makeup_results()}
        {#if terms.some((term) => term.steps.length > 0)}
          {m.chamber_makeup_steps()}
        {:else if basis !== "results"}
          {m.chamber_makeup_no_steps()}
        {/if}
      </p>
    </div>
    <div class="flex" role="group" aria-label={m.chamber_scale()}>
      {#each [false, true] as share (share)}
        <button
          type="button"
          class={cn(
            "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
            {
              "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                isShare === share,
            },
          )}
          aria-pressed={isShare === share}
          onclick={() => (isShare = share)}
          >{share ? m.chamber_scale_share() : m.chamber_scale_seats()}</button
        >
      {/each}
    </div>
  </div>
  <Makeup
    {terms}
    {parties}
    {governments}
    {interim}
    {eras}
    {isShare}
    {selected}
    onselect={(index: number) => (selected = index)}
  />
</section>
