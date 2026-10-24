<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import FlagIcon from "@lucide/svelte/icons/flag";
  import LandmarkIcon from "@lucide/svelte/icons/landmark";
  import MapPinIcon from "@lucide/svelte/icons/map-pin";
  import SearchIcon from "@lucide/svelte/icons/search";
  import UserIcon from "@lucide/svelte/icons/user";
  import VoteIcon from "@lucide/svelte/icons/vote";
  import { Input } from "#lib/components/ui/input/index.js";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const GROUPS = [
    { type: "polity", heading: m.search_group_polity, icon: MapPinIcon },
    { type: "body", heading: m.search_group_body, icon: LandmarkIcon },
    { type: "election", heading: m.search_group_election, icon: VoteIcon },
    { type: "contest", heading: m.search_group_contest, icon: VoteIcon },
    { type: "party", heading: m.search_group_party, icon: FlagIcon },
    { type: "person", heading: m.search_group_person, icon: UserIcon },
  ];

  const best = $derived(data.hits.find((hit) => hit.isBest) ?? null);

  const groups = $derived(
    GROUPS.map((group) => ({
      ...group,
      first: data.hits.findIndex(
        (hit) => hit.type === group.type && !hit.isBest,
      ),
      hits: data.hits.filter(
        (hit) => hit.type === group.type && hit.path != null && !hit.isBest,
      ),
    }))
      .filter((group) => group.hits.length > 0)
      .toSorted((a, b) => a.first - b.first),
  );
</script>

<svelte:head>
  <title
    >{data.q === "" ? m.search_title() : `${data.q} | ${m.search_open()}`} | {m.wordmark()}</title
  >
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="pbe-16">
  <article class="grid grid-cols-12 gap-px overflow-clip border-be bg-border">
    <header class="col-span-12 grid gap-3 bg-card px-3.5 pbs-4 pbe-3.5">
      <h1 class="title">{m.search_title()}</h1>
      <form
        class="grid grid-cols-[minmax(0,1fr)_auto] gap-2 max-inline-2xl"
        method="get"
        action={resolve("/search")}
        role="search"
      >
        <Input
          type="search"
          name="q"
          value={data.q}
          placeholder={m.search_page_placeholder()}
          aria-label={m.search_open()}
          class="block-10"
        />
        <button
          type="submit"
          class="flex items-center gap-1.5 rounded-sm border border-input bg-card px-3 text-sm font-semibold block-10 hover:bg-secondary"
        >
          <SearchIcon class="block-4 inline-4" />
          {m.search_open()}
        </button>
      </form>
      {#if data.q === ""}
        <p class="text-sm text-muted-foreground">{m.search_hint()}</p>
      {:else if groups.length === 0 && best == null}
        <p class="text-sm text-muted-foreground">
          {m.search_empty({ query: data.q })}
        </p>
      {/if}
    </header>

    {#if best != null}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
        aria-labelledby="search-best"
      >
        <h2 id="search-best" class="text-[0.8125rem] font-bold">
          {m.search_best()}
        </h2>
        <a
          class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3.5 py-2 max-inline-2xl hover:bg-secondary"
          href={resolve("/[...path]", { path: best.path ?? "" })}
        >
          {#if best.photo == null}
            <span
              class="row-span-2 grid place-items-center rounded-full bg-muted text-muted-foreground block-16 inline-16"
              aria-hidden="true"
            >
              <UserIcon class="block-6 inline-6" />
            </span>
          {:else}
            <img
              class="row-span-2 rounded-full bg-muted object-cover object-top block-16 inline-16"
              src={best.photo}
              alt=""
              width="64"
              height="64"
            />
          {/if}
          <span class="self-end text-xl font-bold text-pretty">{best.name}</span
          >
          <span class="self-start text-sm text-muted-foreground"
            >{best.office ??
              (best.context == null
                ? ""
                : m.search_person_latest({ contest: best.context }))}</span
          >
        </a>
      </section>
    {/if}

    {#each groups as group (group.type)}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
        aria-labelledby="search-{group.type}"
      >
        <h2 id="search-{group.type}" class="text-[0.8125rem] font-bold">
          {group.heading()}
        </h2>
        <ul class="grid gap-x-6 md:grid-cols-2">
          {#each group.hits as hit (hit.id)}
            <li class="border-be border-border/60">
              <a
                class="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2.5 py-2 hover:bg-secondary"
                href={resolve("/[...path]", { path: hit.path ?? "" })}
              >
                {#if group.type !== "person"}
                  <group.icon
                    class="self-center text-muted-foreground block-4 inline-4"
                    aria-hidden="true"
                  />
                {:else if hit.photo == null}
                  <span
                    class="row-span-2 grid place-items-center self-center rounded-full bg-muted text-muted-foreground block-9 inline-9"
                    aria-hidden="true"
                  >
                    <group.icon class="block-4 inline-4" />
                  </span>
                {:else}
                  <img
                    class="row-span-2 self-center rounded-full bg-muted object-cover object-top block-9 inline-9"
                    src={hit.photo}
                    alt=""
                    loading="lazy"
                    width="36"
                    height="36"
                  />
                {/if}
                <span class="line-clamp-2 font-semibold text-pretty"
                  >{hit.name}</span
                >
                {#if hit.office != null}
                  <span
                    class="col-start-2 truncate text-[0.8125rem] text-muted-foreground"
                    >{hit.office}</span
                  >
                {:else if hit.context != null || hit.date != null}
                  <span
                    class="col-start-2 truncate text-[0.8125rem] text-muted-foreground"
                  >
                    {[
                      hit.type === "person" && hit.context != null
                        ? m.search_person_latest({ contest: hit.context })
                        : hit.context,
                      hit.date == null ? null : date(hit.date),
                    ]
                      .filter((part) => part != null)
                      .join(", ")}
                  </span>
                {/if}
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  </article>
</main>
