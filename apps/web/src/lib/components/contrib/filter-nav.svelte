<script lang="ts">
  import { page } from "$app/state";
  import { cn } from "#lib/utils.js";

  const {
    label,
    name,
    options,
  }: {
    label: string;
    name: string;
    options: Array<{ value: string | null; label: string }>;
  } = $props();

  const current = $derived(page.url.searchParams.get(name));

  const href = (value: string | null) => {
    const search = new URLSearchParams(page.url.search);
    search.delete("before");
    if (value == null) {
      search.delete(name);
    } else {
      search.set(name, value);
    }
    const query = search.toString();
    return query === "" ? page.url.pathname : `${page.url.pathname}?${query}`;
  };
</script>

<nav
  aria-label={label}
  class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-x-3 text-sm"
>
  <span class="text-xs text-muted-foreground" aria-hidden="true">{label}</span>
  <ul class="flex flex-wrap gap-x-4 gap-y-1">
    {#each options as option (option.value)}
      <li>
        <a
          class={cn("underline-offset-4 hover:underline", {
            "font-semibold underline decoration-2": option.value === current,
            "text-muted-foreground hover:text-foreground":
              option.value !== current,
          })}
          href={href(option.value)}
          aria-current={option.value === current ? "true" : undefined}
          >{option.label}</a
        >
      </li>
    {/each}
  </ul>
</nav>
