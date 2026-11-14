<script lang="ts">
  import type { PageData } from "./$types";
  import Release from "./release.svelte";

  const { data }: { data: PageData } = $props();
</script>

<Release
  origin={data.origin}
  releases={data.releases}
  release={data.release}
  isIndex
/>
