<script lang="ts">
  import { cn } from "#lib/utils.js";
  import { copy } from "./copy.js";

  type Line = {
    code: string;
    text: string;
    state: "passed" | "finding" | "failed";
    target?: string;
  };

  const {
    lines,
    onfocus,
  }: { lines: Array<Line>; onfocus?: (target: string) => void } = $props();

  const glyph = { passed: "✓", finding: "!", failed: "✕" };
  const word = {
    passed: copy.check_passed,
    finding: copy.check_finding,
    failed: copy.check_failed,
  };
</script>

<ul class="grid text-sm">
  {#each lines as line (line.code)}
    <li
      class={cn(
        "grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-baseline gap-x-2 border-be py-1.5 last:border-be-0",
        { "font-semibold": line.state !== "passed" },
      )}
    >
      <span
        class={cn(
          "grid place-items-center rounded-full text-[0.625rem] leading-none font-bold block-4 inline-4",
          {
            "border border-foreground/40 text-muted-foreground":
              line.state === "passed",
            "border-2 border-foreground text-foreground":
              line.state === "finding",
            "bg-foreground text-background": line.state === "failed",
          },
        )}
        aria-hidden="true">{glyph[line.state]}</span
      >
      <span class="min-inline-0">
        <span class="font-figures text-xs text-muted-foreground"
          >{line.code}</span
        >
        {#if line.target != null && onfocus != null}
          <button
            type="button"
            class="block text-start underline decoration-border underline-offset-4 hover:decoration-current"
            onclick={() => onfocus(line.target ?? "")}>{line.text}</button
          >
        {:else}
          <span class="block">{line.text}</span>
        {/if}
      </span>
      <span
        class={cn("text-xs", {
          "text-muted-foreground": line.state === "passed",
        })}>{word[line.state]}</span
      >
    </li>
  {/each}
</ul>
