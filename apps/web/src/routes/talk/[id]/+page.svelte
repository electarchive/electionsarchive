<script lang="ts">
  import type { TopicView } from "#lib/contrib.js";
  import CommentComposer from "#lib/components/contrib/comment-composer.svelte";
  import RecordTabs from "#lib/components/contrib/record-tabs.svelte";
  import WatchButton from "#lib/components/contrib/watch-button.svelte";
  import { stateLabel } from "#lib/contrib.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const open = $derived(data.topics.filter((item) => item.status === "open"));
  const resolved = $derived(
    data.topics.filter((item) => item.status === "resolved"),
  );
  const day = (value: string) => date(value.slice(0, 10));
</script>

<svelte:head>
  <title>{m.talk_page_title({ name: data.entity.name })}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet topics(items: Array<TopicView>)}
  <ol class="mbs-2 grid border-bs">
    {#each items as item (item.id)}
      <li class="border-be">
        <a
          class="group grid gap-1 py-3"
          href="/talk/{data.entity.public_id}/{item.id}"
        >
          <span
            class="font-semibold text-pretty underline-offset-4 group-hover:underline"
            >{item.title}</span
          >
          <span
            class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground"
          >
            <span
              >{m.talk_started({
                author: item.author ?? m.proposal_unknown_author(),
                date: day(item.created_at),
              })}</span
            >
            <span class="font-figures"
              >{m.discussion_count({ count: item.comments })}</span
            >
            {#if item.updated_at.slice(0, 10) !== item.created_at.slice(0, 10)}
              <span>{m.talk_updated({ date: day(item.updated_at) })}</span>
            {/if}
          </span>
        </a>
      </li>
    {/each}
  </ol>
{/snippet}

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title text-balance">{data.entity.name}</h1>
    <RecordTabs entity={data.entity} current="talk" />
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.talk_lede()}
    </p>
    {#if data.watch != null}
      <div>
        <WatchButton
          kind={data.watch.kind}
          id={data.entity.id}
          watching={data.watch.watching}
        />
      </div>
    {/if}
  </header>

  <section class="mbs-8" aria-labelledby="talk-open">
    <div class="flex items-baseline justify-between gap-4">
      <h2 id="talk-open" class="heading">{m.talk_open_heading()}</h2>
      {#if open.length > 0}
        <span class="font-figures text-xs text-muted-foreground"
          >{open.length}</span
        >
      {/if}
    </div>
    {#if open.length === 0}
      <p
        class="mbs-2 border-bs pbs-3 text-sm text-pretty text-muted-foreground"
      >
        {m.talk_empty()}
      </p>
    {:else}
      {@render topics(open)}
    {/if}
  </section>

  <section class="mbs-10" aria-labelledby="talk-new">
    <h2 id="talk-new" class="heading">{m.talk_new()}</h2>
    <div class="mbs-3">
      {#if data.commenter === "ready"}
        <CommentComposer
          action="?/comment"
          titled
          placeholder={m.talk_new_body_placeholder()}
          submit={m.talk_new_submit()}
        />
      {:else if data.commenter === "signed_out"}
        <p class="text-sm">
          <a
            class="underline underline-offset-2"
            href="/sign-in?next={encodeURIComponent(
              `/talk/${data.entity.public_id}`,
            )}">{m.talk_sign_in()}</a
          >
        </p>
      {:else}
        <p class="text-sm text-muted-foreground">{m.discussion_verify()}</p>
      {/if}
    </div>
  </section>

  {#if resolved.length > 0}
    <section class="mbs-10" aria-labelledby="talk-resolved">
      <div class="flex items-baseline justify-between gap-4">
        <h2 id="talk-resolved" class="heading">{m.talk_resolved_heading()}</h2>
        <span class="font-figures text-xs text-muted-foreground"
          >{resolved.length}</span
        >
      </div>
      {@render topics(resolved)}
    </section>
  {/if}

  {#if data.proposals.length > 0}
    <section class="mbs-10" aria-labelledby="talk-proposals">
      <h2 id="talk-proposals" class="heading">
        {m.talk_proposals_heading()}
      </h2>
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
              <span class="truncate underline-offset-4 group-hover:underline"
                >{item.title}</span
              >
              <span class="text-xs text-muted-foreground"
                >{stateLabel[item.state]()}</span
              >
            </a>
          </li>
        {/each}
      </ul>
    </section>
  {/if}
</main>
