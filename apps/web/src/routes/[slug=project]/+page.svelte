<script lang="ts">
  import { resolve } from "$app/paths";
  import CiteBody from "#lib/components/cite-body.svelte";
  import Prose from "#lib/components/prose.svelte";
  import TextHead from "#lib/components/text-head.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<TextHead
  title={data.title}
  description={data.lede}
  type={data.slug === "architecture" || data.slug === "indices"
    ? "TechArticle"
    : "Article"}
/>

<main class="mx-auto grid gap-8 px-4 pbs-6 pbe-24 max-inline-7xl max-sm:px-3">
  <nav aria-label={m.footer_project()}>
    <ul class="flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted-foreground">
      {#each data.pages as entry (entry.slug)}
        <li>
          <a
            href={resolve("/[slug=project]", { slug: entry.slug })}
            class={cn(
              "underline decoration-transparent underline-offset-[0.35em] hover:text-foreground hover:decoration-border focus-visible:outline-2 focus-visible:outline-ring",
              {
                "font-semibold text-foreground decoration-foreground decoration-2":
                  entry.slug === data.slug,
              },
            )}
            aria-current={entry.slug === data.slug ? "page" : undefined}
            >{entry.title}</a
          >
        </li>
      {/each}
    </ul>
  </nav>

  <header class="grid gap-3 max-inline-[68ch]">
    <h1 class="title">{data.title}</h1>
    <p class="font-serif text-lg/relaxed text-pretty text-muted-foreground">
      {data.lede}
    </p>
    {#if data.updated != null}
      <p class="text-sm text-muted-foreground">
        {m.project_updated({ date: date(data.updated) })}
      </p>
    {/if}
  </header>

  <div class="grid border-bs-[3px] border-foreground">
    {#each data.sections as section (section.id)}
      <section
        id={section.id}
        aria-labelledby="{section.id}-title"
        class="grid scroll-mbs-6 gap-x-10 gap-y-2 border-be py-5 lg:grid-cols-[15rem_minmax(0,68ch)]"
      >
        <h2 id="{section.id}-title" class="text-lg/snug font-bold text-balance">
          {section.title}
        </h2>
        <div class="grid gap-4">
          <Prose body={section.body} />
          {#if section.isCite === true}
            <div class="max-inline-136">
              <CiteBody
                published={data.site.published}
                release={data.site.release}
              />
            </div>
          {/if}
        </div>
      </section>
    {/each}
  </div>
</main>
