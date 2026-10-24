<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    wording,
  }: {
    wording: Array<{
      kind: "text" | "summary" | "link";
      body: string | null;
      url: string | null;
      publisher: string;
    }>;
  } = $props();

  const uid = $props.id();
  const link = $derived(wording.find((row) => row.kind === "link"));
  let opened = $state<Array<string>>([]);
</script>

<div class="mbs-1 grid gap-3 max-inline-[60ch]">
  {#each wording as block (block.kind)}
    {#if block.kind !== "link" && block.body != null}
      {const isLong = $derived(block.body.length > 240)}
      {const isOpen = $derived(opened.includes(block.kind))}
      <figure class="grid gap-1 border-s-2 border-border ps-3">
        <figcaption class="text-[0.8125rem] font-semibold">
          {block.kind === "text" ? m.wording_text() : m.wording_summary()}
        </figcaption>
        <blockquote
          id="{uid}-{block.kind}"
          class={cn("text-[0.9375rem] text-pretty whitespace-pre-line", {
            "line-clamp-3": isLong && !isOpen,
          })}
        >
          {block.body}
        </blockquote>
        <p
          class="flex flex-wrap gap-x-3 text-[0.8125rem] text-muted-foreground"
        >
          {#if isLong}
            <button
              type="button"
              class="text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
              aria-expanded={isOpen}
              aria-controls="{uid}-{block.kind}"
              onclick={() => {
                opened = isOpen
                  ? opened.filter((kind) => kind !== block.kind)
                  : [...opened, block.kind];
              }}>{isOpen ? m.wording_less() : m.wording_more()}</button
            >
          {/if}
          {#if block.url == null}
            <span>{m.wording_source({ publisher: block.publisher })}</span>
          {:else}
            <a
              class="underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={block.url}
              rel="external noopener"
              >{m.wording_source({ publisher: block.publisher })}</a
            >
          {/if}
        </p>
      </figure>
    {/if}
  {/each}
  {#if link?.url != null}
    <a
      class="justify-self-start text-[0.9375rem] underline decoration-border underline-offset-4 hover:decoration-foreground"
      href={link.url}
      rel="external noopener">{m.wording_link()}</a
    >
  {/if}
</div>
