<script lang="ts">
  import { enhance } from "$app/forms";
  import BountyStatus from "#lib/components/contrib/bounty-status.svelte";
  import WatchButton from "#lib/components/contrib/watch-button.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { CATEGORY_LABELS, GRAIN_LABELS, levelLabel } from "#lib/coverage.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const bounty = $derived(data.bounty);
  const free = $derived(bounty.status === "open" && bounty.claimer == null);
  const facts = $derived(
    [
      {
        term: m.bounty_place(),
        value: bounty.place ?? bounty.polity.toUpperCase(),
        href: bounty.place_path == null ? null : `/${bounty.place_path}`,
      },
      {
        term: m.bounty_missing(),
        value: GRAIN_LABELS[bounty.grain]?.() ?? bounty.grain,
      },
      {
        term: m.bounty_years(),
        value: bounty.years === "" ? null : bounty.years,
      },
      {
        term: m.bounty_level(),
        value:
          bounty.level === "" ? null : levelLabel(bounty.polity, bounty.level),
      },
      {
        term: m.bounty_stage(),
        value: bounty.stage === "" ? null : bounty.stage,
      },
      { term: m.bounty_category(), value: CATEGORY_LABELS[bounty.category]() },
      {
        term: m.bounty_checked(),
        value: bounty.checked == null ? null : date(bounty.checked),
      },
    ].filter((fact) => fact.value != null),
  );

  let pending = $state(false);
  const submit = () => {
    pending = true;
    return async ({ update }: { update: () => Promise<void> }) => {
      await update();
      pending = false;
    };
  };
</script>

<svelte:head>
  <title
    >{m.bounty_title({ number: bounty.number, label: bounty.label })} | {m.wordmark()}</title
  >
  <meta name="description" content={bounty.reason} />
  <link
    rel="alternate"
    type="text/markdown"
    title={bounty.label}
    href="/bounties/{bounty.number}.md"
  />
</svelte:head>

<main class="mx-auto grid gap-8 px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <p class="text-sm">
      <a class="underline underline-offset-2" href="/bounties"
        >{m.bounties_heading()}</a
      >
      <span class="font-figures text-muted-foreground tabular-nums">
        #{bounty.number}</span
      >
    </p>
    <div class="flex items-start justify-between gap-4 max-sm:flex-col">
      <h1 class="title text-pretty">{bounty.label}</h1>
      {#if data.watch != null}
        <WatchButton
          kind="bounty"
          id={bounty.id}
          watching={data.watch.watching}
        />
      {/if}
    </div>
    <p class="max-inline-prose text-pretty">{bounty.reason}</p>
  </header>

  <section
    class="grid gap-3 border-bs-[3px] border-foreground pbs-3"
    aria-labelledby="bounty-state"
  >
    <h2 id="bounty-state" class="heading">{m.bounty_state()}</h2>
    <BountyStatus {bounty} />
    {#if form?.message != null}
      <p class="text-sm text-destructive" role="alert">{form.message}</p>
    {/if}
    {#if bounty.status === "open"}
      {#if data.claimer === "signed_out"}
        <p class="text-sm text-pretty text-muted-foreground">
          <a
            class="underline underline-offset-2"
            href="/sign-in?next={encodeURIComponent(
              `/bounties/${bounty.number}`,
            )}">{m.bounty_sign_in()}</a
          >
        </p>
      {:else if data.claimer === "unverified"}
        <p class="text-sm text-pretty text-muted-foreground">
          <a class="underline underline-offset-2" href="/verify-email"
            >{m.bounty_verify()}</a
          >
        </p>
      {:else if free || data.mine}
        <div class="flex flex-wrap items-center gap-2">
          <form method="POST" action="?/claim" use:enhance={submit}>
            <Button type="submit" disabled={pending}
              >{data.mine ? m.bounty_renew() : m.bounty_claim()}</Button
            >
          </form>
          {#if data.mine}
            <form method="POST" action="?/release" use:enhance={submit}>
              <Button type="submit" variant="outline" disabled={pending}
                >{m.bounty_release()}</Button
              >
            </form>
          {/if}
          <Button variant="outline" href="/edit?bounty={bounty.number}"
            >{m.bounty_propose()}</Button
          >
        </div>
        <p class="max-inline-prose text-xs text-pretty text-muted-foreground">
          {m.bounty_claim_hint()}
        </p>
      {:else}
        <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
          {m.bounty_held_hint()}
        </p>
      {/if}
    {/if}
  </section>

  {#if bounty.category === "blocked" && bounty.status === "open"}
    <p
      class="max-inline-prose border-s-[3px] border-foreground ps-3 text-sm text-pretty"
    >
      {m.bounty_blocked_hint()}
    </p>
  {/if}

  <section
    class="grid gap-3 border-bs-[3px] border-foreground pbs-3"
    aria-labelledby="bounty-facts"
  >
    <h2 id="bounty-facts" class="heading">{m.bounty_facts()}</h2>
    <dl
      class="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-4 text-sm max-sm:grid-cols-1"
    >
      {#each facts as fact (fact.term)}
        <dt
          class="border-be py-1.5 text-muted-foreground max-sm:border-none max-sm:pbe-0"
        >
          {fact.term}
        </dt>
        <dd class="border-be py-1.5 max-sm:pbs-0.5">
          {#if "href" in fact && fact.href != null}
            <a class="underline underline-offset-2" href={fact.href}
              >{fact.value}</a
            >
          {:else}
            {fact.value}
          {/if}
        </dd>
      {/each}
    </dl>
  </section>

  {#if bounty.sources !== ""}
    <section
      class="grid gap-2 border-bs-[3px] border-foreground pbs-3"
      aria-labelledby="bounty-sources"
    >
      <h2 id="bounty-sources" class="heading">{m.bounty_sources()}</h2>
      <p class="max-inline-prose text-sm text-pretty wrap-break-word">
        {bounty.sources}
      </p>
    </section>
  {/if}

  <p class="border-bs pbs-3 text-sm text-pretty text-muted-foreground">
    {m.bounties_footer()}
  </p>
</main>
