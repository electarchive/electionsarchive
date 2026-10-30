<script lang="ts">
  import { resolve } from "$app/paths";
  import { copy } from "./copy.js";
  import { counties } from "./data.js";
  import Scan from "./scan.svelte";

  const mocks = [
    {
      href: resolve("/lab/contrib/panel"),
      title: copy.index_panel_title,
      about: copy.index_panel_about,
      key: "E",
    },
    {
      href: resolve("/lab/contrib/workspace"),
      title: copy.index_workspace_title,
      about: copy.index_workspace_about,
      key: "⌘K",
    },
    {
      href: resolve("/lab/contrib/proposal"),
      title: copy.index_proposal_title,
      about: copy.index_proposal_about,
      key: null,
    },
    {
      href: resolve("/lab/contrib/review"),
      title: copy.index_review_title,
      about: copy.index_review_about,
      key: "J K",
    },
  ];
</script>

<svelte:head>
  <title>{copy.index_title}</title>
</svelte:head>

<div
  class="grid gap-10 px-4 pbs-10 pbe-24 lg:grid-cols-[minmax(0,1fr)_22rem] max-sm:px-3"
>
  <div>
    <h1 class="title">{copy.index_title}</h1>
    <p class="mbs-4 font-serif text-lg/relaxed text-pretty max-inline-[58ch]">
      {copy.index_intro}
    </p>
    <ul class="mbs-8 grid border-bs">
      {#each mocks as mock (mock.href)}
        <li>
          <a
            class="group grid gap-1 border-be py-4 hover:bg-muted/60 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-6 sm:px-2"
            href={mock.href}
          >
            <span class="flex items-baseline gap-2">
              <span
                class="heading group-hover:underline group-hover:underline-offset-4"
                >{mock.title}</span
              >
              {#if mock.key != null}<kbd>{mock.key}</kbd>{/if}
            </span>
            <span
              class="text-pretty text-muted-foreground max-inline-[60ch] group-hover:text-foreground"
              >{mock.about}</span
            >
          </a>
        </li>
      {/each}
    </ul>
  </div>
  <figure class="self-start max-lg:hidden">
    <Scan
      region={[0.02, 0.44, 0.62, 0.745]}
      marks={counties.map((county) => ({
        id: county.id,
        box: county.boxes[0],
        label: county.name,
      }))}
      selected="kankakee"
      alt={copy.ws_scan_alt}
      class="rounded-[2px] border"
    />
    <figcaption class="mbs-2 text-xs text-muted-foreground">
      {copy.panel_source_line}
    </figcaption>
  </figure>
</div>
