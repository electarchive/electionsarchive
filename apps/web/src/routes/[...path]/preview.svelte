<script lang="ts">
  import type { Snippet } from "svelte";
  import { resolve } from "$app/paths";

  const {
    path,
    search = "",
    class: className,
    onclick,
    children,
  }: {
    path: string;
    search?: `?${string}` | "";
    class?: string;
    onclick?: (
      event: MouseEvent & { currentTarget: HTMLAnchorElement },
    ) => void;
    children: Snippet;
  } = $props();
</script>

<a
  class={className}
  href={resolve(`/[...path]${search}`, { path })}
  {onclick}
  data-preview="">{@render children()}</a
>
