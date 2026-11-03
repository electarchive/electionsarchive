<script lang="ts">
  import { page } from "$app/state";
  import FilterNav from "#lib/components/contrib/filter-nav.svelte";
  import Moment from "#lib/components/moment.svelte";
  import { stateLabel, tierLabel } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const query = (changes: Record<string, string | null>) => {
    const search = new URLSearchParams(page.url.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value == null) {
        search.delete(key);
      } else {
        search.set(key, value);
      }
    }
    const text = search.toString();
    return text === "" ? "" : `?${text}`;
  };

  const chips = $derived(
    [
      data.polity == null
        ? null
        : { key: "polity", label: m.proposals_in({ name: data.polity.name }) },
      data.entity == null
        ? null
        : {
            key: "entity",
            label: m.proposals_touching({ name: data.entity.name }),
          },
      data.query.author == null
        ? null
        : {
            key: "author",
            label: m.proposals_by({ handle: data.query.author }),
          },
    ].filter((chip) => chip != null),
  );

  const formats = $derived(query({ before: null }));
</script>

<svelte:head>
  <title>{m.proposals_title()}</title>
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.proposals_feed_title()}
    href="/proposals.atom{formats}"
  />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.proposals_heading()}</h1>
    <div class="grid gap-1.5">
      <FilterNav
        label={m.proposals_filter_state()}
        name="state"
        options={[
          { value: null, label: m.recent_filter_all() },
          ...(
            [
              "open",
              "changes_requested",
              "approved",
              "published",
              "rejected",
              "withdrawn",
              "reverted",
            ] as const
          ).map((state) => ({ value: state, label: stateLabel[state]() })),
        ]}
      />
      <FilterNav
        label={m.recent_filter_tier()}
        name="tier"
        options={[
          { value: null, label: m.recent_filter_all() },
          ...[1, 2, 3].map((tier) => ({
            value: String(tier),
            label: tierLabel[tier]?.() ?? String(tier),
          })),
        ]}
      />
    </div>
    {#if chips.length > 0}
      <ul class="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {#each chips as chip (chip.key)}
          <li>
            {chip.label}
            <a
              class="ms-1.5 text-muted-foreground underline underline-offset-2 hover:text-foreground"
              href="/proposals{query({ [chip.key]: null, before: null })}"
              >{m.proposals_clear()}</a
            >
          </li>
        {/each}
      </ul>
    {/if}
    <p class="flex flex-wrap gap-x-4 text-sm">
      <a class="underline underline-offset-2" href="/proposals.atom{formats}"
        >{m.recent_subscribe()}</a
      >
      <a
        class="text-muted-foreground underline underline-offset-2 hover:text-foreground"
        href="/recent">{m.proposals_recent()}</a
      >
      <a
        class="text-muted-foreground underline underline-offset-2 hover:text-foreground"
        href="/bounties">{m.bounties_heading()}</a
      >
    </p>
  </header>

  {#if data.proposals.length === 0}
    <p class="mbs-6 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {m.proposals_empty()}
    </p>
  {:else}
    <ol class="mbs-6 grid border-bs">
      {#each data.proposals as item (item.number)}
        <li class="border-be">
          <a
            class="group grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 py-2.5 text-sm max-sm:grid-cols-[3rem_minmax(0,1fr)]"
            href="/proposals/{item.number}"
          >
            <span class="font-figures text-muted-foreground"
              >#{item.number}</span
            >
            <span class="grid gap-0.5 min-inline-0">
              <span
                class="font-semibold text-pretty underline-offset-4 group-hover:underline"
                >{item.title}</span
              >
              <span
                class="flex flex-wrap gap-x-3 text-xs text-muted-foreground"
              >
                <span>{item.author ?? m.feed_someone()}</span>
                {#if item.agent_name != null}
                  <span>{m.proposals_agent({ agent: item.agent_name })}</span>
                {/if}
                {#if item.polity != null}
                  <span>{item.polity}</span>
                {/if}
                <Moment at={item.submitted_at} style="date" />
                <span class="font-figures"
                  >{m.proposals_changes({ count: item.changes })}</span
                >
              </span>
            </span>
            <span
              class="grid justify-items-end text-xs max-sm:col-start-2 max-sm:justify-items-start"
            >
              <span
                class={cn({
                  "font-semibold":
                    item.state === "published" || item.state === "open",
                  "text-muted-foreground":
                    item.state !== "published" && item.state !== "open",
                })}>{stateLabel[item.state]()}</span
              >
              {#if item.tier != null}
                <span class="text-muted-foreground"
                  >{tierLabel[item.tier]?.()}</span
                >
              {/if}
            </span>
          </a>
        </li>
      {/each}
    </ol>
  {/if}

  {#if data.next != null}
    <p class="mbs-4 text-sm">
      <a
        class="underline underline-offset-2"
        href="/proposals{query({ before: data.next })}">{m.proposals_older()}</a
      >
    </p>
  {/if}
</main>
