<script lang="ts">
  import { resolve } from "$app/paths";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { moment } from "#lib/site.js";
  import { cn } from "#lib/utils.js";

  const {
    live,
    published,
  }: {
    live: { state: "updating" | "paused" | "off"; newest: string | null };
    published: string | null;
  } = $props();

  const summary = $derived(
    {
      updating: m.status_live_updating,
      paused: m.status_live_paused,
      off: m.status_live_off,
    }[live.state](),
  );
</script>

{#snippet dot(className?: string)}
  <span
    aria-hidden="true"
    class={cn(
      "inline-block shrink-0 rounded-full block-2 inline-2",
      {
        "bg-(--yes)": live.state === "updating",
        "bg-done-half": live.state === "paused",
        "border border-muted-foreground": live.state === "off",
      },
      className,
    )}
  ></span>
{/snippet}

<Popover.Root>
  <Popover.Trigger
    class="-m-1.5 grid place-items-center rounded-full p-1.5 hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring data-[state=open]:bg-secondary"
    aria-label={`${m.status_title()}: ${summary}`}
  >
    {@render dot()}
  </Popover.Trigger>
  <Popover.Content
    align="start"
    side="top"
    class="grid gap-2 p-3 text-[0.8125rem]/snug inline-72"
  >
    <p class="text-[0.9375rem] font-semibold">{m.status_title()}</p>
    <div class="grid grid-cols-[auto_1fr] items-baseline gap-x-2 gap-y-0.5">
      {@render dot("translate-y-[-0.05rem]")}
      <p>{summary}</p>
      {#if live.state !== "off" && live.newest != null}
        <span></span>
        <p class="font-figures text-muted-foreground tabular-nums">
          {m.status_live_newest({ time: moment(live.newest) })}
        </p>
      {/if}
    </div>
    {#if published != null}
      <div class="grid gap-0.5 border-bs pbs-2">
        <p class="font-figures tabular-nums">
          {m.status_data({ time: moment(published) })}
        </p>
        <a
          href={resolve("/recent")}
          class="justify-self-start font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          >{m.status_recent()}</a
        >
      </div>
    {/if}
  </Popover.Content>
</Popover.Root>
