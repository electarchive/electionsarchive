<script lang="ts">
  import WatchButton from "#lib/components/contrib/watch-button.svelte";
  import Markdown from "#lib/components/markdown.svelte";
  import { stateLabel } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const name = $derived(data.profile.display_name ?? data.profile.handle);
  const tallies = $derived([
    { label: m.contributor_accepted(), value: data.counts.accepted },
    { label: m.contributor_open(), value: data.counts.open },
    { label: m.contributor_rejected(), value: data.counts.rejected },
    { label: m.contributor_reverted(), value: data.counts.reverted },
  ]);
</script>

<svelte:head>
  <title>{m.contributor_page_title({ name })}</title>
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.contributor_feed_title({ name })}
    href="/contributors/{data.profile.handle}.atom"
  />
  {#if data.counts.accepted === 0}
    <meta name="robots" content="noindex" />
  {/if}
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-1.5">
    <h1 class="title text-balance">{name}</h1>
    <p
      class="flex flex-wrap gap-x-4 gap-y-0.5 text-sm text-pretty text-muted-foreground"
    >
      <span class="font-semibold text-foreground">@{data.profile.handle}</span>
      {#if data.profile.affiliation != null}
        <span>{data.profile.affiliation}</span>
      {/if}
      <span>{m.account_level({ level: String(data.profile.level) })}</span>
      {#if data.profile.limited}
        <span class="font-semibold text-foreground">{m.account_limited()}</span>
      {/if}
      <span
        >{m.contributor_since({
          date: date(data.profile.member_since.slice(0, 7)),
        })}</span
      >
    </p>
    {#if data.mine}
      <p class="text-sm">
        <a class="underline underline-offset-2" href="/settings/profile"
          >{m.contributor_edit()}</a
        >
      </p>
    {:else if data.watch != null}
      <div>
        <WatchButton
          kind="user"
          id={data.watch.id}
          watching={data.watch.watching}
        />
      </div>
    {/if}
  </header>

  <dl
    class="mbs-6 grid grid-cols-4 gap-px border-y bg-border max-sm:grid-cols-2"
  >
    {#each tallies as tally (tally.label)}
      <div
        class="grid gap-0.5 bg-background py-3 ps-3 first:ps-0 max-sm:nth-3:ps-0"
      >
        <dt class="text-xs text-muted-foreground">{tally.label}</dt>
        <dd class="font-figures text-2xl/none font-semibold">
          {count(tally.value)}
        </dd>
      </div>
    {/each}
  </dl>

  {#if data.profile.bio_md != null || data.profile.links.length > 0}
    <section class="mbs-8 grid gap-3" aria-label={m.contributor_about()}>
      {#if data.profile.bio_md != null}
        <div class="max-inline-prose text-[0.9375rem]">
          <Markdown body={data.profile.bio_md} />
        </div>
      {/if}
      {#if data.profile.links.length > 0}
        <ul class="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {#each data.profile.links as link (link.url)}
            <li>
              <a
                class="underline decoration-border underline-offset-2 hover:decoration-foreground"
                href={link.url}
                rel="me nofollow ugc noopener noreferrer">{link.label}</a
              >
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}

  <section class="mbs-10" aria-labelledby="contributor-proposals">
    <div class="flex items-baseline justify-between gap-4">
      <h2 id="contributor-proposals" class="heading">
        {m.contributor_proposals()}
      </h2>
      {#if data.proposals.length > 0}
        <span class="font-figures text-xs text-muted-foreground"
          >{count(data.proposals.length)}</span
        >
      {/if}
    </div>
    {#if data.proposals.length === 0}
      <p
        class="mbs-2 border-bs pbs-3 text-sm text-pretty text-muted-foreground"
      >
        {m.contributor_no_proposals()}
      </p>
    {:else}
      <ul class="mbs-2 grid border-bs">
        {#each data.proposals as item (item.number)}
          <li class="border-be">
            <a
              class="group grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 py-2.5 text-sm"
              href="/proposals/{item.number}"
            >
              <span class="font-figures text-muted-foreground"
                >#{item.number}</span
              >
              <span class="grid min-inline-0">
                <span class="truncate underline-offset-4 group-hover:underline"
                  >{item.title}</span
                >
                {#if item.agent_name != null}
                  <span class="truncate text-xs text-muted-foreground"
                    >{m.contributor_via_agent({ agent: item.agent_name })}</span
                  >
                {/if}
              </span>
              <span class="text-xs text-muted-foreground"
                >{stateLabel[item.state]()}</span
              >
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  {#if data.agents.length > 0}
    <section class="mbs-10" aria-labelledby="contributor-agents">
      <h2 id="contributor-agents" class="heading">
        {m.contributor_agents()}
      </h2>
      <p class="mbs-1 text-sm text-pretty text-muted-foreground">
        {m.contributor_agents_lede()}
      </p>
      <ul class="mbs-2 grid border-bs">
        {#each data.agents as agent (agent.name)}
          <li
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be py-2.5 text-sm"
          >
            <span class="grid min-inline-0">
              <span class="truncate font-semibold">{agent.name}</span>
              {#if agent.model != null}
                <span class="truncate text-xs text-muted-foreground"
                  >{agent.model}</span
                >
              {/if}
            </span>
            <span class="font-figures text-xs text-muted-foreground"
              >{m.contributor_agent_proposals({ count: agent.proposals })}</span
            >
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <p class="mbs-10 text-sm text-pretty text-muted-foreground">
    <a
      class="underline underline-offset-2"
      href="/contributors/{data.profile.handle}.atom">{m.contributor_feed()}</a
    >
  </p>
</main>
