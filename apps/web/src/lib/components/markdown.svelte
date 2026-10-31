<script lang="ts">
  import type { RootContent } from "mdast";
  import { parseMarkdown, piecesOf, safeHref } from "#lib/markdown.js";

  const { body }: { body: string } = $props();

  const tree = $derived(parseMarkdown(body));
</script>

{#snippet nodes(list: Array<RootContent>)}
  {#each list as node, index (index)}
    {@render one(node)}
  {/each}
{/snippet}

{#snippet one(node: RootContent)}
  {#if node.type === "paragraph"}
    <p>{@render nodes(node.children)}</p>
  {:else if node.type === "text"}
    {#each piecesOf(node.value) as piece, index (index)}
      {#if piece.kind === "mention"}
        <a
          class="font-semibold underline-offset-2 hover:underline"
          href="/contributors/{piece.value}">@{piece.value}</a
        >
      {:else if piece.kind === "reference"}
        <a
          class="font-figures underline-offset-2 hover:underline"
          href="/proposals/{piece.value}">#{piece.value}</a
        >
      {:else}
        {piece.value}
      {/if}
    {/each}
  {:else if node.type === "strong"}
    <strong class="font-semibold">{@render nodes(node.children)}</strong>
  {:else if node.type === "emphasis"}
    <em>{@render nodes(node.children)}</em>
  {:else if node.type === "delete"}
    <s>{@render nodes(node.children)}</s>
  {:else if node.type === "inlineCode"}
    <code class="rounded-sm bg-muted px-1 font-mono text-[0.9em]"
      >{node.value}</code
    >
  {:else if node.type === "code"}
    <pre
      class="overflow-x-auto rounded-sm bg-muted px-3 py-2 font-mono text-xs/relaxed"><code
        >{node.value}</code
      ></pre>
  {:else if node.type === "blockquote"}
    <blockquote class="border-s-2 border-border ps-3 text-muted-foreground">
      {@render nodes(node.children)}
    </blockquote>
  {:else if node.type === "list"}
    {#if node.ordered === true}
      <ol class="list-decimal ps-5" start={node.start ?? 1}>
        {@render nodes(node.children)}
      </ol>
    {:else}
      <ul class="list-disc ps-5">{@render nodes(node.children)}</ul>
    {/if}
  {:else if node.type === "listItem"}
    <li class="[&>p]:inline">{@render nodes(node.children)}</li>
  {:else if node.type === "link" || node.type === "linkReference"}
    {const href = $derived(node.type === "link" ? safeHref(node.url) : null)}
    {#if href == null}
      {@render nodes(node.children)}
    {:else}
      <a
        class="underline decoration-border underline-offset-2 hover:decoration-foreground"
        {href}
        rel="nofollow ugc noopener noreferrer"
        target="_blank">{@render nodes(node.children)}</a
      >
    {/if}
  {:else if node.type === "break"}
    <br />
  {:else if node.type === "thematicBreak"}
    <hr class="border-border" />
  {:else if node.type === "html"}
    {node.value}
  {/if}
{/snippet}

<div class="grid gap-2 text-pretty wrap-break-word">
  {@render nodes(tree.children)}
</div>
