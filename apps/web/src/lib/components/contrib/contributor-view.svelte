<script lang="ts">
  import type { Root } from "mdast";
  import type { Snippet } from "svelte";
  import type { ProposalState } from "#lib/contrib.js";
  import Markdown from "#lib/components/markdown.svelte";
  import { stateLabel } from "#lib/contrib.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    profile,
    bio,
    work,
    actions,
  }: {
    profile: {
      handle: string;
      display_name: string | null;
      affiliation: string | null;
      links: Array<{ url: string; label: string }>;
      member_since: string;
      level: number | null;
      limited: boolean;
    };
    bio: Root | null;
    work: {
      counts: {
        accepted: number;
        open: number;
        rejected: number;
        reverted: number;
      };
      proposals: Array<{
        number: number;
        title: string;
        state: ProposalState;
        agent_name: string | null;
      }>;
      bounties: Array<{ number: number; label: string; proposal: number }>;
      agents: Array<{ name: string; model: string | null; proposals: number }>;
    } | null;
    actions?: Snippet;
  } = $props();

  const name = $derived(profile.display_name ?? profile.handle);
  const tallies = $derived(
    work == null
      ? []
      : [
          { label: m.contributor_accepted(), value: work.counts.accepted },
          { label: m.contributor_open(), value: work.counts.open },
          { label: m.contributor_rejected(), value: work.counts.rejected },
          { label: m.contributor_reverted(), value: work.counts.reverted },
        ],
  );
</script>

<header class="grid gap-1.5">
  <h1 class="title text-balance">{name}</h1>
  <p
    class="flex flex-wrap gap-x-4 gap-y-0.5 text-sm text-pretty text-muted-foreground"
  >
    <span class="font-semibold text-foreground">@{profile.handle}</span>
    {#if profile.affiliation != null}
      <span>{profile.affiliation}</span>
    {/if}
    {#if profile.level != null}
      <span>{m.account_level({ level: String(profile.level) })}</span>
    {/if}
    {#if profile.limited}
      <span class="font-semibold text-foreground">{m.account_limited()}</span>
    {/if}
    <span
      >{m.contributor_since({
        date: date(profile.member_since.slice(0, 7)),
      })}</span
    >
  </p>
  {@render actions?.()}
</header>

{#if work != null}
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
{/if}

{#if bio != null || profile.links.length > 0}
  <section class="mbs-8 grid gap-3" aria-label={m.contributor_about()}>
    {#if bio != null}
      <div class="max-inline-prose text-[0.9375rem]">
        <Markdown tree={bio} />
      </div>
    {/if}
    {#if profile.links.length > 0}
      <ul class="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {#each profile.links as link (link.url)}
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

{#if work != null}
  <section class="mbs-10" aria-labelledby="contributor-proposals">
    <div class="flex items-baseline justify-between gap-4">
      <h2 id="contributor-proposals" class="heading">
        {m.contributor_proposals()}
      </h2>
      {#if work.proposals.length > 0}
        <span class="font-figures text-xs text-muted-foreground"
          >{count(work.proposals.length)}</span
        >
      {/if}
    </div>
    {#if work.proposals.length === 0}
      <p
        class="mbs-2 border-bs pbs-3 text-sm text-pretty text-muted-foreground"
      >
        {m.contributor_no_proposals()}
      </p>
    {:else}
      <ul class="mbs-2 grid border-bs">
        {#each work.proposals as item (item.number)}
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

  {#if work.bounties.length > 0}
    <section class="mbs-10" aria-labelledby="contributor-bounties">
      <div class="flex items-baseline justify-between gap-4">
        <h2 id="contributor-bounties" class="heading">
          {m.contributor_bounties()}
        </h2>
        <span class="font-figures text-xs text-muted-foreground"
          >{count(work.bounties.length)}</span
        >
      </div>
      <ul class="mbs-2 grid border-bs">
        {#each work.bounties as item (item.number)}
          <li
            class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be py-2.5 text-sm"
          >
            <span class="font-figures text-muted-foreground"
              >#{item.number}</span
            >
            <a
              class="truncate underline-offset-4 hover:underline"
              href="/bounties/{item.number}">{item.label}</a
            >
            <a
              class="font-figures text-xs text-muted-foreground underline-offset-4 hover:underline"
              href="/proposals/{item.proposal}"
              >{m.contributor_bounty_proposal({ number: item.proposal })}</a
            >
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if work.agents.length > 0}
    <section class="mbs-10" aria-labelledby="contributor-agents">
      <h2 id="contributor-agents" class="heading">
        {m.contributor_agents()}
      </h2>
      <p class="mbs-1 text-sm text-pretty text-muted-foreground">
        {m.contributor_agents_lede()}
      </p>
      <ul class="mbs-2 grid border-bs">
        {#each work.agents as agent (agent.name)}
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
      href="/contributors/{profile.handle}.atom">{m.contributor_feed()}</a
    >
  </p>
{/if}
