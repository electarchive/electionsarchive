<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";

  const {
    src,
    alt,
    type = "image/png",
  }: { src: string; alt: string; type?: "image/png" | "image/jpeg" } = $props();
</script>

<svelte:head>
  <meta property="og:site_name" content={m.wordmark()} />
  <meta property="og:locale" content="en_US" />
  <meta property="og:image" content={src} />
  <meta property="og:image:type" content={type} />
  <meta property="og:image:width" content="2400" />
  <meta property="og:image:height" content="1260" />
  <meta property="og:image:alt" content={alt} />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content={src} />
  <meta name="twitter:image:alt" content={alt} />
</svelte:head>
