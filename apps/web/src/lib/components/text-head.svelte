<script lang="ts">
  import { page } from "$app/state";
  import ShareImage from "#lib/components/share-image.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { TEXT_PAGES } from "#lib/text-pages.js";

  const {
    title,
    description,
    type = "Article",
    crumbs = [],
    image,
  }: {
    title: string;
    description: string;
    type?: "Article" | "TechArticle";
    crumbs?: Array<{ name: string; path: string }>;
    image?: { src: string; alt: string; type?: "image/png" | "image/jpeg" };
  } = $props();

  const origin = $derived(page.url.origin);
  const canonical = $derived(`${origin}${page.url.pathname}`);
  const dates = $derived(TEXT_PAGES[page.url.pathname]);
  const share = $derived(
    image ?? {
      src: `${origin}/og.jpg`,
      alt: m.home_og_alt(),
      type: "image/jpeg" as const,
    },
  );
  const jsonld = $derived(
    JSON.stringify({
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": type,
          "@id": `${canonical}#article`,
          headline: title,
          description,
          url: canonical,
          mainEntityOfPage: canonical,
          datePublished: dates?.published,
          dateModified: dates?.modified,
          inLanguage: getLocale(),
          image: share.src,
          isPartOf: { "@id": `${origin}/#website` },
          author: { "@id": `${origin}/#organization` },
          publisher: {
            "@type": "Organization",
            "@id": `${origin}/#organization`,
            name: m.wordmark(),
            url: `${origin}/`,
            logo: {
              "@type": "ImageObject",
              url: `${origin}/apple-touch-icon.png`,
            },
          },
        },
        {
          "@type": "BreadcrumbList",
          itemListElement: [
            { name: m.title(), path: "/" },
            ...crumbs,
            { name: title, path: page.url.pathname },
          ].map((crumb, index) => ({
            "@type": "ListItem",
            position: index + 1,
            name: crumb.name,
            item: `${origin}${crumb.path}`,
          })),
        },
      ],
    })
      .replaceAll("<", String.raw`<`)
      .replaceAll("&", String.raw`&`),
  );
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <link rel="alternate" type="text/markdown" href="{canonical}.md" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content={title} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  {#if dates != null}
    <meta property="article:published_time" content={dates.published} />
    <meta property="article:modified_time" content={dates.modified} />
  {/if}
  <meta name="twitter:title" content={title} />
  <meta name="twitter:description" content={description} />
  <svelte:element this={"script"} type="application/ld+json"
    >{jsonld}</svelte:element
  >
</svelte:head>

<ShareImage src={share.src} alt={share.alt} type={share.type} />
