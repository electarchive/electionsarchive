<script lang="ts">
  import { browser } from "$app/env";
  import { READ_ONLY } from "$app/env/public";
  import { goto, invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { useQueryClient } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { Await } from "svelte-islands";
  import { toast } from "svelte-sonner";
  import type { Primed } from "#lib/primed.js";
  import { arrive } from "#lib/arrive.js";
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

  if (!browser) {
    untrack(() => {
      prime(useQueryClient(), primed);
    });
  }

  const staying = { reset: false, replace: true } as const;
  let landed: { key: string; url: URL } | null = null;

  function offer(missed: URL) {
    toast.error(m.contest_view_failed(), {
      action: {
        label: m.contest_body_retry(),
        onClick: () => {
          const from = page.url.href;
          void arrive(
            missed,
            async () => goto(missed, staying),
            () => page.url.href === from,
          ).then((isReady) => {
            if (!isReady) {
              offer(missed);
            }
          });
        },
      },
    });
  }

  function settle<T>(value: T, key: string, url: URL) {
    landed = { key, url };
    return value;
  }

  async function guard<T>(promise: Promise<T>, key: string, url: URL) {
    try {
      return settle(await promise, key, url);
    } catch (error) {
      const last = landed;
      if (
        last == null ||
        last.key !== key ||
        last.url.href === url.href ||
        page.url.href !== url.href
      ) {
        throw error;
      }
      void arrive(
        last.url,
        async () => goto(last.url, staying),
        () => page.url.href === url.href,
      ).then(() => {
        offer(url);
      });
      return new Promise<never>(() => {});
    }
  }

  const shown = $derived.by((): typeof body => {
    const key = `${id}:${unit}`;
    const url = untrack(() => new URL(page.url.href));
    return body instanceof Promise
      ? guard(body, key, url)
      : settle(body, key, url);
  });

  const shape = $derived(
    unit === "" ? "whole" : head.scope.isFine ? "fine" : "unit",
  );
</script>

<Await
  promise={shown}
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
