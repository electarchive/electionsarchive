<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";

  const {
    kinds,
  }: {
    kinds: Array<
      "current" | "upload" | "new" | "moved" | "gap" | "overlap" | "parent"
    >;
  } = $props();

  const labels = {
    current: m.ws_bd_key_current,
    upload: m.ws_bd_key_upload,
    new: m.ws_bd_key_new,
    moved: m.ws_bd_key_moved,
    gap: m.ws_bd_key_gap,
    overlap: m.ws_bd_key_overlap,
    parent: m.ws_bd_key_parent,
  };
</script>

<ul
  class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
  aria-label={m.ws_bd_legend()}
>
  {#each kinds as kind (kind)}
    <li class="flex items-center gap-1.5">
      <span class="swatch {kind}" aria-hidden="true"></span>{labels[kind]()}
    </li>
  {/each}
</ul>

<style>
  .swatch {
    --ink: color-mix(in oklab, var(--foreground) 45%, transparent);

    display: inline-block;
    inline-size: 0.875rem;
    block-size: 0.625rem;
    border: 1px solid var(--foreground);
  }

  .current {
    border-color: color-mix(in oklab, var(--foreground) 38%, transparent);
  }

  .upload {
    background: color-mix(in oklab, var(--foreground) 5%, transparent);
  }

  .new {
    background: color-mix(in oklab, var(--marker) 70%, transparent);
  }

  .moved,
  .gap,
  .overlap {
    border-color: color-mix(in oklab, var(--foreground) 60%, transparent);
  }

  .moved {
    background: repeating-linear-gradient(
      -45deg,
      var(--ink) 0 1px,
      transparent 1px 4px
    );
  }

  .gap {
    background: repeating-linear-gradient(
      45deg,
      var(--ink) 0 1px,
      transparent 1px 4px
    );
  }

  .overlap {
    background:
      repeating-linear-gradient(-45deg, var(--ink) 0 1px, transparent 1px 4px),
      repeating-linear-gradient(45deg, var(--ink) 0 1px, transparent 1px 4px);
  }

  .parent {
    block-size: 0;
    border: 0;
    border-block-start: 2px dashed var(--destructive);
  }
</style>
