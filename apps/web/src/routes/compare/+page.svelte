<script lang="ts">
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import ChevronsUpDownIcon from "@lucide/svelte/icons/chevrons-up-down";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { idle } from "#lib/idle.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import type SearchPanel from "./search.svelte";

  const { data }: { data: PageData } = $props();

  const opened = $state([false, false]);
  let Search = $state<typeof SearchPanel | null>(null);

  function load() {
    void import("./search.svelte")
      .then((module) => {
        Search = module.default;
      })
      .catch(() => {});
  }

  $effect(() => idle(load));

  const preload = async (type: string | undefined) =>
    type === "person"
      ? import("./people.svelte")
      : type === "polity"
        ? import("./places.svelte")
        : type === "party"
          ? import("./parties.svelte")
          : null;

  const sides = $derived(data.sides);
  const isMismatch = $derived(
    sides.length === 2 && sides[0]?.type !== sides[1]?.type,
  );

  const pick = (index: number, id: string) => {
    const ids = [sides[0]?.id ?? null, sides[1]?.id ?? null];
    ids[index] = id;
    const text = ids
      .flatMap((value, at) =>
        value == null ? [] : [`${at === 0 ? "a" : "b"}=${value}`],
      )
      .join("&");
    opened[index] = false;
    void goto(resolve(`/compare?${text}`));
  };
</script>

<svelte:head>
  <title
    >{sides.length === 2
      ? `${sides[0]?.name} – ${sides[1]?.name}`
      : m.versus_title()} | {m.wordmark()}</title
  >
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet picker(index: number)}
  {const other = $derived(sides[1 - index] ?? null)}
  {const side = $derived(sides[index] ?? null)}
  {const kind = $derived(other?.type ?? side?.type ?? null)}
  <Popover.Root
    bind:open={opened[index]}
    onOpenChange={(isOpen: boolean) => {
      if (isOpen) {
        load();
      }
    }}
  >
    <Popover.Trigger>
      {#snippet child({ props })}
        <button
          {...props}
          type="button"
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-sm border border-input bg-card px-3 text-start block-14 hover:bg-secondary"
        >
          <span
            class={cn("bg-foreground block-2.5 inline-2.5", {
              "rounded-full": index === 0,
            })}
            aria-hidden="true"
          ></span>
          {#if side == null}
            <span class="truncate text-muted-foreground"
              >{kind === "person"
                ? m.versus_pick_person()
                : kind === "polity"
                  ? m.versus_pick_place()
                  : kind === "party"
                    ? m.versus_pick_party()
                    : m.versus_pick()}</span
            >
          {:else}
            <span class="grid text-start min-inline-0">
              <span class="truncate text-lg/tight font-[720]">{side.name}</span>
              {#if side.context != null}
                <span class="truncate text-xs text-muted-foreground"
                  >{side.context}</span
                >
              {/if}
            </span>
          {/if}
          <ChevronsUpDownIcon class="text-muted-foreground block-4 inline-4" />
        </button>
      {/snippet}
    </Popover.Trigger>
    <Popover.Content
      class="p-0 inline-(--bits-popover-anchor-width) min-inline-72"
      align="start"
    >
      {#if Search != null}
        <Search
          {kind}
          other={other?.id ?? null}
          onpick={(id: string) => pick(index, id)}
        />
      {/if}
    </Popover.Content>
  </Popover.Root>
{/snippet}

<main class="pbe-16">
  <article class="grid grid-cols-12 gap-px overflow-clip border-be bg-border">
    <header class="col-span-12 grid gap-3 bg-card px-3.5 pbs-4 pbe-3.5">
      <div class="grid gap-1">
        <h1 class="title">
          {m.versus_title()}
        </h1>
        <p class="text-[0.9375rem] text-muted-foreground max-inline-[64ch]">
          {m.versus_lede()}
        </p>
      </div>
      <div
        class="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 max-sm:grid-cols-[minmax(0,1fr)]"
      >
        {@render picker(0)}
        <button
          type="button"
          class="grid place-items-center justify-self-center rounded-sm text-muted-foreground block-9 inline-9 hover:bg-secondary hover:text-foreground disabled:opacity-40"
          disabled={sides.length < 2}
          onclick={async () =>
            goto(
              resolve(
                `/compare?a=${sides[1]?.id ?? ""}&b=${sides[0]?.id ?? ""}`,
              ),
            )}
        >
          <ArrowLeftRightIcon class="block-4 inline-4 max-sm:rotate-90" />
          <span class="sr-only">{m.versus_swap()}</span>
        </button>
        {@render picker(1)}
      </div>
    </header>

    {#if data.examples.length > 0}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
        aria-labelledby="examples"
      >
        <h2 id="examples" class="text-[0.8125rem] font-bold">
          {m.versus_examples()}
        </h2>
        <ul class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {#each data.examples as example (example.a)}
            <li class="grid">
              <a
                class="grid grid-cols-[auto_minmax(0,1fr)] content-start items-center gap-x-2.5 gap-y-1.5 rounded-sm border border-input px-3 py-2.5 hover:border-foreground hover:bg-secondary"
                href={resolve(`/compare?a=${example.a}&b=${example.b}`)}
              >
                {#each example.sides as side, at (side.id)}
                  {#if side.photo != null}
                    <img
                      class="justify-self-center rounded-full bg-muted object-cover object-top ring-2 ring-(--party) ring-offset-1 ring-offset-card block-7 inline-7"
                      style:--party={swatch(side.color, side.party ?? side.id)}
                      src={side.photo}
                      alt=""
                      loading="lazy"
                      width="28"
                      height="28"
                    />
                  {:else}
                    <span
                      class={cn("justify-self-center block-3 inline-3", {
                        "rounded-full": at === 0,
                        "rounded-xs": at === 1 && side.color != null,
                        "bg-foreground": side.color == null,
                      })}
                      style:background={side.color == null
                        ? null
                        : swatch(side.color, side.party ?? side.id)}
                      aria-hidden="true"
                    ></span>
                  {/if}
                  <span class="truncate text-base font-[720]">{side.name}</span>
                {/each}
                {#if example.year != null}
                  <span
                    class="col-span-2 pbs-0.5 text-[0.8125rem] text-muted-foreground"
                    >{(example.kind === "person"
                      ? m.versus_example_person
                      : example.kind === "party"
                        ? m.versus_example_party
                        : m.versus_example_polity)({
                      year: String(example.year),
                    })}</span
                  >
                {/if}
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
    {#if data.rivals.length > 0}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3 pbe-3.5"
        aria-labelledby="rivals"
      >
        <h2 id="rivals" class="text-[0.8125rem] font-bold">
          {sides[0]?.type === "person"
            ? m.versus_opponents()
            : sides[0]?.type === "polity"
              ? m.versus_neighbors()
              : m.versus_rivals()}
        </h2>
        <ul class="flex flex-wrap gap-2">
          {#each data.rivals as rival (rival.id)}
            <li>
              <a
                class="inline-flex items-center gap-2 rounded-sm border border-input px-3 text-base font-[720] block-10 hover:border-foreground hover:bg-secondary"
                href={resolve(`/compare?a=${sides[0]?.id ?? ""}&b=${rival.id}`)}
                ><span
                  class="bg-foreground block-2.5 inline-2.5"
                  aria-hidden="true"
                ></span>{rival.name}</a
              >
            </li>
          {/each}
        </ul>
      </section>
    {/if}
    {#if isMismatch}
      <p class="col-span-12 bg-card px-3.5 py-4 text-sm text-muted-foreground">
        {m.versus_mismatch()}
      </p>
    {:else if sides.length === 2}
      {#await Promise.all([data.body, preload(sides[0]?.type)])}
        <div
          class="col-span-12 grid gap-px"
          role="status"
          aria-label={m.versus_loading()}
        >
          <div class="bg-card block-44 motion-safe:animate-pulse"></div>
          <div class="bg-card block-56 motion-safe:animate-pulse"></div>
          <div class="bg-card block-80 motion-safe:animate-pulse"></div>
        </div>
      {:then [body]}
        {#if body.people != null}
          {#await import("./people.svelte") then view}
            <view.default people={body.people} />
          {/await}
        {:else if body.places != null}
          {#await import("./places.svelte") then view}
            <view.default data={body.places} />
          {/await}
          {#await import("./system.svelte") then system}
            <system.default
              sides={sides.map(({ id, name }) => ({ id, name }))}
            />
          {/await}
        {:else if body.parties != null}
          {#await import("./parties.svelte") then view}
            <view.default data={body.parties} first={sides[0]} />
          {/await}
        {/if}
      {/await}
    {/if}
  </article>
</main>
