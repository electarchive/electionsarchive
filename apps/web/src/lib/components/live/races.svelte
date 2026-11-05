<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import RacePicker from "./race-picker.svelte";

  const {
    summaries,
    current = null,
    counties = [],
    isDistrict = false,
    back,
  }: {
    summaries: Array<Summary>;
    current?: string | null;
    isDistrict?: boolean;
    counties?: Array<{
      id: string;
      name: string;
      href: string;
      leader: LiveOption | null;
      margin: number | null;
      counted: number | null;
    }>;
    back: { href: string; label: string };
  } = $props();
</script>

<div
  class="grid gap-3 border-be border-border bg-background px-4 py-3.5 sm:gap-4 sm:px-8"
>
  <a
    href={back.href}
    class="-ms-1 inline-flex items-center gap-0.5 justify-self-start rounded-xs pe-1 text-sm font-semibold text-foreground hover:underline"
    ><ChevronLeftIcon
      class="block-4 inline-4"
      aria-hidden="true"
    />{back.label}</a
  >
  {#if summaries.length > 1}
    <RacePicker {summaries} {current} label={m.night_rail_label()} isCompact />
  {/if}

  {#if counties.length > 0}
    <div
      class="flex items-center gap-2 border-bs border-border pbs-3 sm:flex-wrap max-sm:-mx-4 max-sm:overflow-x-auto max-sm:px-4 max-sm:pbe-1"
    >
      <span class="me-2 shrink-0 text-[0.8125rem] font-semibold"
        >{isDistrict
          ? m.night_races_districts()
          : m.night_races_counties()}</span
      >
      {#each counties as county (county.id)}
        <a
          href={county.href}
          class="flex shrink-0 items-center gap-2 rounded-full py-1 ps-2 pe-3 ring-1 ring-border transition-colors ring-inset hover:bg-secondary"
        >
          <span
            class="shrink-0 rounded-full block-2 inline-2"
            style:background-color={county.leader == null
              ? "var(--border)"
              : tone(county.leader)}
          ></span>
          <span class="text-[0.8125rem] font-semibold">{county.name}</span>
          {#if county.leader != null}
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{surname(county.leader)}{#if county.margin != null}
                +{lead(county.margin)}{/if}</span
            >
          {/if}
          <span
            class="relative overflow-hidden rounded-xs bg-muted block-1 inline-8"
            title={county.counted == null
              ? m.night_nation_waiting()
              : m.night_races_counted({ share: percent(county.counted, 0) })}
          >
            <span
              class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{(county.counted ?? 0) * 100}%"
            ></span>
          </span>
        </a>
      {/each}
    </div>
  {/if}
</div>
