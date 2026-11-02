<script lang="ts">
  import { enhance } from "$app/forms";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import Discussion from "#lib/components/contrib/discussion.svelte";
  import Moment from "#lib/components/moment.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { moment } from "#lib/zone.svelte.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const isOpen = $derived(data.topic.status === "open");
  let busy = $state(false);
</script>

<svelte:head>
  <title
    >{data.topic.title} · {m.talk_page_title({ name: data.entity.name })}</title
  >
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <p class="text-sm">
      <a
        class="inline-flex items-center gap-1.5 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        href="/talk/{data.entity.public_id}"
        ><ArrowLeftIcon class="block-3.5 inline-3.5" />{m.talk_back({
          name: data.entity.name,
        })}</a
      >
    </p>
    <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <p class="text-sm font-semibold" role="status">
        {isOpen ? m.talk_status_open() : m.talk_status_resolved()}
      </p>
      {#if data.canResolve}
        <form
          method="POST"
          action="?/status"
          use:enhance={() => {
            busy = true;
            return async ({ update }) => {
              await update();
              busy = false;
            };
          }}
        >
          <input
            type="hidden"
            name="status"
            value={isOpen ? "resolved" : "open"}
          />
          <Button type="submit" variant="outline" size="sm" disabled={busy}
            >{isOpen ? m.talk_resolve() : m.talk_reopen()}</Button
          >
        </form>
      {/if}
    </div>
    <h1 class="title text-balance">{data.topic.title}</h1>
    <p class="text-sm text-muted-foreground">
      <Moment
        at={data.topic.created_at}
        label={m.talk_started({
          author: data.topic.author ?? m.proposal_unknown_author(),
          date: moment(data.topic.created_at, "date"),
        })}
      />
    </p>
  </header>

  <Discussion
    comments={data.comments}
    commenter={data.commenter}
    canComment={data.commenter === "ready"}
    canModerate={data.canModerate}
  />
</main>
