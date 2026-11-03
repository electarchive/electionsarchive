<script lang="ts">
  import ContributorView from "#lib/components/contrib/contributor-view.svelte";
  import WatchButton from "#lib/components/contrib/watch-button.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const name = $derived(data.profile.display_name ?? data.profile.handle);
</script>

<svelte:head>
  <title>{m.contributor_page_title({ name })}</title>
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.contributor_feed_title({ name })}
    href="/contributors/{data.profile.handle}.atom"
  />
  {#if data.work.counts.accepted === 0}
    <meta name="robots" content="noindex" />
  {/if}
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <ContributorView profile={data.profile} bio={data.bio} work={data.work}>
    {#snippet actions()}
      {#if data.mine}
        <div class="mbs-1.5">
          <Button href="/settings/profile?edit" variant="outline" size="sm"
            >{m.profile_edit()}</Button
          >
        </div>
      {:else if data.watch != null}
        <div>
          <WatchButton
            kind="user"
            id={data.watch.id}
            watching={data.watch.watching}
          />
        </div>
      {/if}
    {/snippet}
  </ContributorView>
</main>
