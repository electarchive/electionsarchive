<script lang="ts">
  import { page } from "$app/state";
  import CopyIcon from "@lucide/svelte/icons/copy";
  import { toast } from "svelte-sonner";
  import type { Format } from "#lib/site.js";
  import { copy } from "#lib/clipboard.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cite, FORMATS } from "#lib/site.js";

  const {
    published,
    release,
  }: {
    published: string | null;
    release: { calver: string; doi: string | null } | null;
  } = $props();
  let format = $state<Format>("APA");

  const text = $derived(
    cite(format, {
      origin: page.url.origin,
      published,
      release,
      today: new Date(),
    }),
  );
</script>

<div class="grid gap-3">
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
