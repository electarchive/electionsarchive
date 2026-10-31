<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import Preview from "./preview.svelte";

  const { id }: { id: string } = $props();

  const uid = $props.id();

  const alongside = createQuery(() =>
    orpc.elections.alongside.queryOptions({ input: { id } }),
  );

  const groups = $derived(alongside.data ?? []);
</script>

{#if groups.length > 0}
  <section
    class="col-span-12 grid content-start gap-4 bg-card px-3.5 pbs-3 pbe-4"
    aria-labelledby="{uid}-title"
  >
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="text-[0.8125rem] font-bold">
        {m.alongside_title()}
      </h2>
      <p
        class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
      >
        {m.alongside_lede()}
      </p>
    </div>
    <div class="columns-sm gap-x-8">
      {#each groups as group (group.path ?? group.name)}
        <div class="grid break-inside-avoid content-start gap-1 pbe-5">
          <h3 class="text-sm font-semibold">
            {#if group.path == null}
              {group.name}
            {:else}
              <a
                class="underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                href={resolve("/[...path]", { path: group.path })}
                >{group.name}</a
              >
            {/if}
          </h3>
          <ul class="grid text-sm">
            {#each group.contests as contest, index (index)}
              <li
                class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 border-be border-border/60 py-1.5 last:border-be-0 pointer-coarse:py-2"
              >
                {#if contest.path == null}
                  <span>{contest.name}</span>
                {:else}
                  <Preview
                    class="underline decoration-border underline-offset-2 hover:decoration-foreground"
                    path={contest.path}>{contest.name}</Preview
                  >
                {/if}
                {#if contest.leader != null}
                  <span
                    class="flex items-baseline gap-2 justify-self-end text-end"
                  >
                    <span
                      class={cn("text-[0.8125rem]", {
                        "font-semibold": contest.isWon,
                        "text-muted-foreground": !contest.isWon,
                      })}>{contest.leader}</span
                    >
                    {#if contest.share != null}
                      <span
                        class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                        >{percent(contest.share, 1)}</span
                      >
                    {/if}
                  </span>
                {/if}
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </div>
  </section>
{/if}
