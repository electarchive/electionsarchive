<script lang="ts">
  type Piece =
    | { kind: "text" | "strong"; text: string; href?: undefined }
    | { kind: "link"; text: string; href: string };

  const { body }: { body: string } = $props();

  function pieces(text: string) {
    const found: Array<Piece> = [];
    let last = 0;
    for (const match of text.matchAll(
      /\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/gu,
    )) {
      if (match.index > last) {
        found.push({ kind: "text", text: text.slice(last, match.index) });
      }
      found.push(
        match[1] == null
          ? { kind: "link", text: match[2] ?? "", href: match[3] ?? "" }
          : { kind: "strong", text: match[1] },
      );
      last = match.index + match[0].length;
    }
    if (last < text.length) {
      found.push({ kind: "text", text: text.slice(last) });
    }
    return found;
  }

  const blocks = $derived(
    body.split("\n\n").map((chunk) => {
      const lines = chunk.split("\n");
      const isOrdered = /^\d+\. /u.test(lines[0] ?? "");
      return isOrdered || lines[0]?.startsWith("- ") === true
        ? {
            isList: true,
            isOrdered,
            items: lines.map((line) =>
              pieces(line.replace(/^(?:- |\d+\. )/u, "")),
            ),
          }
        : { isList: false, isOrdered, items: [pieces(chunk)] };
    }),
  );
</script>

{#snippet inline(list: Array<Piece>)}
  {#each list as piece, index (index)}
    {#if piece.kind === "strong"}
      <strong class="font-sans font-semibold text-foreground"
        >{piece.text}</strong
      >
    {:else if piece.kind === "link"}
      <a
        class="underline decoration-border underline-offset-[0.2em] hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-ring"
        href={piece.href}
        rel={piece.href.startsWith("/") ? undefined : "external"}
        >{piece.text}</a
      >
    {:else}
      {piece.text}
    {/if}
  {/each}
{/snippet}

<div class="grid gap-3 font-serif text-[1.0625rem]/relaxed text-pretty">
  {#each blocks as block, index (index)}
    {#if !block.isList}
      <p>{@render inline(block.items[0] ?? [])}</p>
    {:else if block.isOrdered}
      <ol class="grid gap-2">
        {#each block.items as item, position (position)}
          <li class="grid grid-cols-[1.75rem_minmax(0,1fr)]">
            <span
              class="pbs-[0.1em] font-figures text-[0.9375rem] font-bold tabular-nums"
              aria-hidden="true">{position + 1}</span
            >
            <span>{@render inline(item)}</span>
          </li>
        {/each}
      </ol>
    {:else}
      <ul class="grid gap-2">
        {#each block.items as item, position (position)}
          <li
            class="relative ps-4 before:absolute before:inset-s-0 before:inset-bs-[0.72em] before:bg-foreground before:block-px before:inline-2"
          >
            {@render inline(item)}
          </li>
        {/each}
      </ul>
    {/if}
  {/each}
</div>
