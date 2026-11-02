<script lang="ts">
  import MessagesSquareIcon from "@lucide/svelte/icons/messages-square";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    talk,
    compact = false,
  }: { talk: { id: string; open: number } | null; compact?: boolean } =
    $props();
</script>

{#if talk != null}
  <a
    class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
    href="/talk/{talk.id}"
    aria-label={talk.open > 0 ? m.talk_link({ count: talk.open }) : undefined}
    ><MessagesSquareIcon class="block-3.5 inline-3.5" /><span
      class={cn({ "max-md:sr-only": compact })}>{m.talk_action()}</span
    >{#if talk.open > 0}<span
        class="font-figures font-normal text-muted-foreground">{talk.open}</span
      >{/if}</a
  >
{/if}
