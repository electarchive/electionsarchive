<script lang="ts">
  import { READ_ONLY } from "$app/env/public";
  import { invalidateAll } from "$app/navigation";
  import { useQueryClient } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { Await } from "svelte-islands";
  import type { Primed } from "#lib/primed.js";
  import EditPanel from "#lib/components/contrib/edit-panel.svelte";
  import TalkLink from "#lib/components/contrib/talk-link.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { prime } from "#lib/primed.js";
  import type { PageServerData } from "./$types";
  import ContestBody from "./contest-body.svelte";
  import ContestTitle from "./contest-title.svelte";

  const {
    id,
    talk,
    unit,
    head,
    body,
    primed,
    ballot,
    standing,
    crowned,
    match,
  }: {
    id: string;
    talk: { id: string; open: number } | null;
    unit: string;
    head: Extract<PageServerData, { kind: "contest" }>["head"];
    body: Extract<PageServerData, { kind: "contest" }>["body"];
    primed: Primed | Promise<Primed>;
    ballot: Extract<PageServerData, { kind: "contest" }>["ballot"];
    standing: Extract<PageServerData, { kind: "contest" }>["standing"];
    crowned: Extract<PageServerData, { kind: "contest" }>["crowned"];
    match: string;
  } = $props();

  const client = useQueryClient();

  function seed(found: Primed | Promise<Primed>) {
    if (found instanceof Promise) {
      void found.then((ready) => {
        prime(client, ready);
      });
    } else {
      prime(client, found);
    }
  }

  untrack(() => {
    seed(primed);
  });

  $effect.pre(() => {
    seed(primed);
  });

  const shape = $derived(
    unit === "" ? "whole" : head.scope.isFine ? "fine" : "unit",
  );
</script>

<Await
  promise={body}
  name="contest.body:{head.kind}:{shape}"
  key="{id}:{unit}"
  reserve="60rem"
>
  {#snippet children(opened)}
    <ContestBody
      {id}
      {talk}
      {unit}
      contest={opened.contest}
      lists={opened.lists}
      rounds={opened.rounds}
      {ballot}
      cycle={opened.cycle}
      handover={opened.handover}
      additional={opened.additional}
      successors={opened.successors}
      statutes={opened.statutes}
      boundaries={opened.boundaries}
      surround={opened.surround}
      {match}
      {standing}
      {crowned}
      sameDay={opened.sameDay}
    />
  {/snippet}
  {#snippet pending(skeleton)}
    <header
      class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end"
    >
      <div
        class="col-start-1 row-start-1 grid gap-1.5 min-inline-0 md:row-span-2"
      >
        <ContestTitle {head} />
      </div>
      {#if !READ_ONLY}
        <div
          class="col-start-1 row-start-1 flex gap-2 self-start justify-self-end md:col-start-2"
        >
          <TalkLink {talk} compact />
          <EditPanel
            entity={id}
            label={head.name}
            title={m.edit_title_contest()}
          />
        </div>
      {/if}
    </header>
    {@render skeleton()}
  {/snippet}
  {#snippet failed()}
    <header
      class="col-span-12 grid gap-x-8 gap-y-3 bg-card px-3.5 pbs-4 pbe-3.5"
    >
      <div class="grid gap-1.5 min-inline-0">
        <ContestTitle {head} />
      </div>
      <p
        class="flex flex-wrap items-baseline gap-x-3 text-[0.9375rem] text-muted-foreground"
      >
        {m.contest_body_failed()}
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
