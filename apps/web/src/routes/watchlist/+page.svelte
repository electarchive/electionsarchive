<script lang="ts">
  import { enhance } from "$app/forms";
  import FeedList from "#lib/components/contrib/feed-list.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ActionData, PageData } from "./$types";

  const { data, form }: { data: PageData; form: ActionData } = $props();

  const kindLabel = {
    entity: m.watchlist_kind_entity,
    polity: m.watchlist_kind_polity,
    proposal: m.watchlist_kind_proposal,
    user: m.watchlist_kind_user,
    bounty: m.watchlist_kind_bounty,
  };

  const hrefOf = (target: (typeof data.targets)[number]) => {
    if (target.kind === "proposal") {
      return target.number == null ? null : `/proposals/${target.number}`;
    }
    if (target.kind === "bounty") {
      return target.number == null ? null : `/bounties/${target.number}`;
    }
    if (target.kind === "user") {
      return target.handle == null ? null : `/contributors/${target.handle}`;
    }
    return target.path == null
      ? target.public_id == null
        ? null
        : `/talk/${target.public_id}`
      : `/${target.path}`;
  };

  let copied = $state(false);
</script>

<svelte:head>
  <title>{m.watchlist_title()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.watchlist_heading()}</h1>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.watchlist_lede()}
    </p>
  </header>

  {#if data.mine.length > 0}
    <section class="mbs-8" aria-labelledby="watchlist-mine">
      <h2 id="watchlist-mine" class="mbe-2 text-base font-semibold">
        {m.watchlist_mine()}
      </h2>
      <FeedList entries={data.mine} label={m.watchlist_mine()} />
    </section>
  {/if}

  <section class="mbs-8" aria-labelledby="watchlist-stream">
    <h2 id="watchlist-stream" class="mbe-2 text-base font-semibold">
      {m.watchlist_stream()}
    </h2>
    {#if data.entries.length === 0}
      <p class="border-bs pbs-3 text-sm text-pretty text-muted-foreground">
        {data.targets.length === 0 ? m.watchlist_empty() : m.watchlist_quiet()}
      </p>
    {:else}
      <FeedList entries={data.entries} label={m.watchlist_stream()} />
    {/if}
    {#if data.next != null}
      <p class="mbs-4 text-sm">
        <a
          class="underline underline-offset-2"
          href="/watchlist?before={encodeURIComponent(data.next)}"
          >{m.recent_older()}</a
        >
      </p>
    {/if}
  </section>

  <section class="mbs-10" aria-labelledby="watchlist-targets">
    <h2 id="watchlist-targets" class="mbe-2 text-base font-semibold">
      {m.watchlist_targets()}
    </h2>
    {#if data.targets.length === 0}
      <p class="border-bs pbs-3 text-sm text-pretty text-muted-foreground">
        {m.watchlist_targets_empty()}
      </p>
    {:else}
      <ul class="grid border-bs text-sm">
        {#each data.targets as target (`${target.kind}:${target.id}`)}
          {const href = $derived(hrefOf(target))}
          <li
            class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-be py-1.5"
          >
            <span class="grid min-inline-0">
              {#if href == null}
                <span class="truncate">{target.name ?? target.id}</span>
              {:else}
                <a class="truncate underline-offset-4 hover:underline" {href}
                  >{target.name ?? target.id}</a
                >
              {/if}
              <span class="text-xs text-muted-foreground"
                >{kindLabel[target.kind]()}</span
              >
            </span>
            <form method="POST" action="?/unwatch" use:enhance>
              <input type="hidden" name="kind" value={target.kind} />
              <input type="hidden" name="id" value={target.id} />
              <Button
                type="submit"
                variant="ghost"
                size="sm"
                aria-label={m.watchlist_unwatch_named({
                  name: target.name ?? target.id,
                })}>{m.watch_stop()}</Button
              >
            </form>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  {#if data.feed != null}
    <section class="mbs-10 grid gap-2" aria-labelledby="watchlist-feed">
      <h2 id="watchlist-feed" class="text-base font-semibold">
        {m.watchlist_feed()}
      </h2>
      <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
        {m.watchlist_feed_lede()}
      </p>
      <div class="flex flex-wrap items-center gap-2">
        <input
          class="flex-1 rounded-sm border border-input bg-transparent px-2 font-figures text-xs block-8 min-inline-0"
          readonly
          value={data.feed}
          aria-label={m.watchlist_feed()}
          onfocus={(event) => event.currentTarget.select()}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onclick={async () => {
            try {
              await navigator.clipboard.writeText(data.feed ?? "");
              copied = true;
            } catch {
              copied = false;
            }
          }}
          >{copied
            ? m.watchlist_feed_copied()
            : m.watchlist_feed_copy()}</Button
        >
        <form method="POST" action="?/rotate" use:enhance>
          <Button type="submit" variant="ghost" size="sm"
            >{m.watchlist_feed_rotate()}</Button
          >
        </form>
      </div>
      <p class="text-xs text-muted-foreground" role="status">
        {#if form != null && "rotated" in form}{m.watchlist_feed_rotated()}{/if}
      </p>
    </section>
  {:else}
    <p class="mbs-10 text-sm text-pretty text-muted-foreground">
      {m.watchlist_feed_needs_profile()}
      <a class="underline underline-offset-2" href="/settings/profile"
        >{m.profile_nav()}</a
      >
    </p>
  {/if}
</main>
