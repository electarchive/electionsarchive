<script lang="ts">
  import { cn } from "#lib/utils.js";

  const {
    before,
    after,
    n,
    label,
    lang,
    class: className,
    onopen,
  }: {
    before: string | null;
    after: string | null;
    n?: number;
    label?: string;
    lang?: string;
    class?: string;
    onopen?: () => void;
  } = $props();
</script>

<span
  class={cn("inline-flex items-baseline gap-1.5 font-figures", className)}
  {lang}
  dir={lang == null ? undefined : "auto"}
>
  {#if before != null}
    <s class="text-muted-foreground decoration-foreground/50">{before}</s>
  {/if}
  {#if after != null}
    <ins
      class="font-semibold text-foreground no-underline shadow-[inset_0_-2px_0_var(--foreground)]"
      >{after}</ins
    >
  {/if}
  {#if n != null}
    {#if onopen == null}
      <sup class="text-[0.6875rem] font-semibold text-muted-foreground">{n}</sup
      >
    {:else}
      <button
        type="button"
        class="-translate-y-1.5 self-start rounded-xs px-0.5 text-[0.6875rem] leading-none font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label={label}
        onclick={onopen}>{n}</button
      >
    {/if}
  {/if}
</span>
