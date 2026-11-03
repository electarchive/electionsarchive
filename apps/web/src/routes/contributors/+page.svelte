<script lang="ts">
  import { Button } from "#lib/components/ui/button/index.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const nextHref = $derived.by(() => {
    if (data.next == null) {
      return null;
    }
    const search = new URLSearchParams({ after: data.next });
    if (data.q !== "") {
      search.set("q", data.q);
    }
    return `/contributors?${search}`;
  });
</script>

<svelte:head>
  <title>{m.contributors_title()}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.contributors_heading()}</h1>
    <form method="GET" role="search" class="flex gap-2 max-inline-md">
      <Input
        name="q"
        type="search"
        value={data.q}
        maxlength={60}
        aria-label={m.contributors_search_label()}
        placeholder={m.contributors_search_placeholder()}
      />
      <Button type="submit" variant="outline">{m.contributors_search()}</Button>
    </form>
  </header>

  {#if data.contributors.length === 0}
    <p class="mbs-6 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {data.q === "" ? m.contributors_empty() : m.contributors_no_match()}
    </p>
  {:else}
    <ul class="mbs-6 grid border-bs">
      {#each data.contributors as person (person.handle)}
        <li class="border-be">
          <a
            class="group grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2.5 text-sm"
            href="/contributors/{person.handle}"
          >
            <span class="grid min-inline-0">
              <span
                class="truncate font-semibold underline-offset-4 group-hover:underline"
                >{person.display_name ?? person.handle}</span
              >
              <span class="truncate text-xs text-muted-foreground"
                >@{person.handle}{#if person.affiliation != null}<span
                    class="ps-3">{person.affiliation}</span
                  >{/if}</span
              >
            </span>
            <span class="grid justify-items-end text-xs text-muted-foreground">
              <span class="font-figures"
                >{m.contributors_accepted({ count: person.accepted })}</span
              >
              <span
                >{m.contributor_since({
                  date: date(person.member_since.slice(0, 7)),
                })}</span
              >
            </span>
          </a>
        </li>
      {/each}
    </ul>
  {/if}

  {#if nextHref != null}
    <p class="mbs-4 text-sm">
      <a class="underline underline-offset-2" href={nextHref}
        >{m.contributors_next()}</a
      >
    </p>
  {/if}
</main>
