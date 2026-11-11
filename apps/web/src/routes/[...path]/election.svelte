<script lang="ts">
  import { browser } from "$app/env";
  import { invalidateAll } from "$app/navigation";
  import { useQueryClient } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { Await } from "svelte-islands";
  import type { Primed } from "#lib/primed.js";
  import { m } from "#lib/paraglide/messages.js";
  import { prime } from "#lib/primed.js";
  import type { PageData, PageServerData } from "./$types";
  import ElectionBody from "./election-body.svelte";
  import ElectionTitle from "./election-title.svelte";

  const {
    id,
    path,
    query,
    chosen,
    scope,
    head,
    body,
    primed,
    sameDay,
    sections,
    live,
  }: {
    id: string;
    path: string;
    query: string;
    chosen: string | null;
    scope: { id: string; path: string } | null;
    head: Extract<PageServerData, { kind: "election" }>["head"];
    body: Extract<PageServerData, { kind: "election" }>["body"];
    primed: Primed | Promise<Primed>;
    sameDay: Extract<PageServerData, { kind: "election" }>["sameDay"];
    sections: Extract<PageData, { kind: "election" }>["sections"];
    live: Extract<PageServerData, { kind: "election" }>["live"];
  } = $props();

  if (!browser) {
    untrack(() => {
      prime(useQueryClient(), primed);
    });
  }
</script>

<Await
  promise={body}
  name="election.body:{head.kind}:{scope == null ? 'whole' : 'scoped'}"
  key="{id}{query}"
  reserve="60rem"
>
  {#snippet children(opened)}
    <ElectionBody
      {id}
      {path}
      {query}
      {chosen}
      {scope}
      election={opened.election}
      cycle={opened.cycle}
      series={opened.series}
      ledger={opened.ledger}
      {sameDay}
      {sections}
      {live}
    />
  {/snippet}
  {#snippet pending(skeleton)}
    <header
      class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end"
    >
      <ElectionTitle {head} />
    </header>
    {@render skeleton()}
  {/snippet}
  {#snippet failed()}
    <header
      class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5"
    >
      <ElectionTitle {head} />
      <p
        class="flex flex-wrap items-baseline gap-x-3 text-[0.9375rem] text-muted-foreground"
      >
        {m.election_body_failed()}
        <button
          class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          type="button"
          onclick={() => {
            void invalidateAll();
          }}>{m.contest_body_retry()}</button
        >
      </p>
    </header>
  {/snippet}
</Await>
