<script lang="ts">
  import { page } from "$app/state";
  import CopyIcon from "@lucide/svelte/icons/copy";
  import { toast } from "svelte-sonner";
  import type { Format } from "#lib/site.js";
  import { copy } from "#lib/clipboard.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cite, FORMATS } from "#lib/site.js";

  const {
    published,
    release,
  }: {
    published: string | null;
    release: { calver: string; doi: string | null } | null;
  } = $props();

  let isOpen = $state(false);
  let format = $state<Format>("APA");

  const narrow = $derived(isNarrowLayout());
  const text = $derived(
    isOpen
      ? cite(format, {
          origin: page.url.origin,
          published,
          release,
          today: new Date(),
        })
      : "",
  );
</script>

{#snippet body()}
  <div class="grid gap-3 p-3">
    <div class="grid gap-0.5">
      <p class="text-[0.9375rem] font-semibold">{m.footer_cite()}</p>
      <p class="text-[0.8125rem] text-pretty text-muted-foreground">
        {m.footer_cite_lede()}
      </p>
    </div>
    <Segmented
      options={FORMATS.map((value) => ({ value, label: value }))}
      bind:value={format}
      label={m.footer_cite()}
      size="sm"
      fill
    />
    <pre
      class="overflow-auto rounded-sm border bg-secondary px-2.5 py-2 font-figures text-xs/relaxed wrap-break-word whitespace-pre-wrap max-block-60">{text}</pre>
    <Button
      variant="outline"
      size="sm"
      class="justify-self-start"
      onclick={() => {
        void copy(text).then(() =>
          toast.success(m.footer_cite_copied({ format })),
        );
      }}
      ><CopyIcon class="block-3.5 inline-3.5" />{m.footer_cite_copy({
        format,
      })}</Button
    >
  </div>
{/snippet}

{#if narrow}
  <Drawer.Root bind:open={isOpen}>
    <Drawer.Trigger
      class="ms-auto rounded-xs underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring max-sm:ms-0"
      >{m.footer_cite()}</Drawer.Trigger
    >
    <Drawer.Content>
      <Drawer.Header class="sr-only">
        <Drawer.Title>{m.footer_cite()}</Drawer.Title>
      </Drawer.Header>
      <div class="pbs-1 pbe-3">
        {@render body()}
      </div>
    </Drawer.Content>
  </Drawer.Root>
{:else}
  <Popover.Root bind:open={isOpen}>
    <Popover.Trigger
      class="ms-auto rounded-xs underline decoration-transparent underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring max-sm:ms-0"
      >{m.footer_cite()}</Popover.Trigger
    >
    <Popover.Content
      align="end"
      side="top"
      class="gap-0 overflow-hidden p-0 inline-96"
    >
      {@render body()}
    </Popover.Content>
  </Popover.Root>
{/if}
