<script lang="ts">
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";
  import Local from "./local.svelte";

  const { data }: { data: Extract<PageData, { kind: "day" }> } = $props();

  const governments = $derived(
    data.local.tiers.flatMap((tier) => tier.governments),
  );
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1 class="title">
    {m.local_title({ year: data.date.slice(0, 4), place: data.local.name })}
  </h1>
  <p class="text-[0.9375rem] text-muted-foreground">
    <time datetime={data.date}>{date(data.date)}</time>
  </p>
  <p class="text-[1.0625rem] text-pretty max-inline-[64ch]">
    {m.local_lede({
      governments: m.local_governments({ count: governments.length }),
      contests: m.race_contests({
        count: governments.flatMap((government) => government.contests).length,
      }),
    })}
  </p>
</header>
<Local local={data.local} isOwn={false} />
