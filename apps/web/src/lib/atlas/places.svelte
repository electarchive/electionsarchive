<script lang="ts">
  import { count, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    title,
    rows,
    year,
    onpick,
    onhover,
  }: {
    title: string | null;
    rows: Array<{
      k: number;
      name: string;
      valid: number;
      parts: Array<{ key: string; tone: string; share: number }>;
      leader: string;
      tone: string;
      margin: number | null;
      swing: number | null;
      isFlipped: boolean;
    }>;
    year: string | null;
    onpick: (k: number) => void;
    onhover: (k: number | null) => void;
  } = $props();
</script>

<section class="grid grid-cols-[minmax(0,1fr)] gap-1.5">
  {#if title != null}
    <h3 class="text-[0.8125rem] font-semibold text-muted-foreground">
      {title}
    </h3>
  {/if}
  <ul class="grid grid-cols-[minmax(0,1fr)] border-bs">
    {#each rows as row (row.k)}
      <li class="border-be">
        <button
          type="button"
          class="grid grid-cols-[minmax(0,1fr)] gap-1 px-1 py-2 text-start inline-full hover:bg-secondary focus-visible:bg-secondary"
          onclick={() => onpick(row.k)}
          onpointerenter={() => onhover(row.k)}
          onpointerleave={() => onhover(null)}
          onfocus={() => onhover(row.k)}
          onblur={() => onhover(null)}
        >
          <span class="flex items-baseline gap-2 text-[0.8125rem]">
            <span class="flex-1 truncate font-semibold">{row.name}</span>
            {#if row.valid > 0}
              <span
                class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
                >{count(row.valid)}</span
              >
            {/if}
          </span>
          <span
            class="flex overflow-clip rounded-xs bg-secondary block-1.5"
            aria-hidden="true"
          >
            {#each row.parts as part (part.key)}
              <span
                class="block-full"
                style:background={part.tone}
                style:inline-size="{part.share * 100}%"
              ></span>
            {/each}
          </span>
          {#if row.margin != null}
            <span class="flex items-center gap-1.5 text-xs">
              <span
                class="shrink-0 rounded-xs block-2 inline-2"
                style:background={row.tone}
              ></span>
              <span class="truncate">{row.leader}</span>
              <span class="font-figures font-semibold tabular-nums"
                >{points(row.margin)}</span
              >
              {#if row.isFlipped && year != null}
                <span class="text-muted-foreground"
                  >{m.atlas_flipped({ year })}</span
                >
              {:else if row.swing != null && year != null}
                <span
                  class="font-figures text-muted-foreground tabular-nums"
                  title={m.atlas_since({ year })}>{points(row.swing)}</span
                >
              {/if}
            </span>
          {/if}
        </button>
      </li>
    {/each}
  </ul>
</section>
