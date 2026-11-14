<script lang="ts">
  import Release from "#routes/downloads/release.svelte";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

<Release
  origin={data.origin}
  releases={data.releases}
  release={data.release}
  isIndex={false}
/>
