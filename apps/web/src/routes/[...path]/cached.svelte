<script lang="ts" generics="T">
  import type { QueryKey } from "@tanstack/svelte-query";
  import type { Snippet } from "svelte";
  import { useQueryClient } from "@tanstack/svelte-query";
  import { untrack } from "svelte";

  const {
    key,
    value,
    children,
  }: { key: QueryKey; value: T; children: Snippet } = $props();

  const client = useQueryClient();
  untrack(() => client.setQueryData(key, value));
</script>

{@render children()}
