<script lang="ts">
  import XIcon from "@lucide/svelte/icons/x";
  import * as v from "valibot";
  import type { Change, ChangeGroup } from "#lib/server/downloads.js";
  import { Button } from "#lib/components/ui/button/index.js";
  import { changeLabel, countryName } from "#lib/downloads.js";
  import { count, date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    calver,
    groups,
    initial,
  }: {
    calver: string;
    groups: Array<ChangeGroup>;
    initial: { changes: Array<Change>; next: number | null };
  } = $props();

  const Page = v.object({
    changes: v.array(
      v.object({
        id: v.string(),
        pipeline: v.nullable(v.string()),
        kind: v.string(),
        summary: v.string(),
        published_at: v.string(),
        path: v.nullable(v.string()),
      }),
    ),
    next: v.nullable(v.number()),
  });

  let country = $state<string | null>(null);
  let pipeline = $state<string | null>(null);
  let items = $derived(initial.changes);
  let next = $derived(initial.next);
  let status = $state<"idle" | "loading" | "failed">("idle");
  let request = 0;
  let isResetting = false;

  const kinds = $derived.by(() => {
    const totals = new Map<string, number>();
    for (const group of groups) {
      totals.set(group.kind, (totals.get(group.kind) ?? 0) + group.count);
    }
    return [...totals].toSorted((a, b) => b[1] - a[1]);
  });
  const countries = $derived.by(() => {
    const totals = new Map<
      string,
      { code: string; count: number; pipelines: Map<string, number> }
    >();
    for (const group of groups) {
      const code = group.country ?? "-";
      const row = totals.get(code) ?? {
        code,
        count: 0,
        pipelines: new Map<string, number>(),
      };
      row.count += group.count;
      const key = group.pipeline ?? "";
      row.pipelines.set(key, (row.pipelines.get(key) ?? 0) + group.count);
      totals.set(code, row);
    }
    return [...totals.values()]
      .map((row) => ({
        ...row,
        label:
          row.code === "-"
            ? m.downloads_changes_no_country()
            : countryName(row.code),
      }))
      .toSorted((a, b) => b.count - a.count);
  });
  const active = $derived(countries.find((row) => row.code === country));
  const pipelines = $derived(
    active == null
      ? []
      : [...active.pipelines]
          .filter(([key]) => key !== "")
          .toSorted((a, b) => b[1] - a[1]),
  );
  const most = $derived(countries[0]?.count ?? 1);

  async function load(isReset: boolean) {
    const ticket = ++request;
    isResetting = isReset;
    status = "loading";
    const search = new URLSearchParams();
    if (!isReset && next != null) {
      search.set("offset", String(next));
    }
    if (country != null) {
      search.set("country", country);
    }
    if (pipeline != null) {
      search.set("pipeline", pipeline);
    }
    try {
      const response = await fetch(
        `/downloads/${calver}/changes.json?${search}`,
      );
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const page = v.parse(Page, await response.json());
      if (ticket !== request) {
        return;
      }
      items = isReset ? page.changes : [...items, ...page.changes];
      next = page.next;
      status = "idle";
    } catch {
      if (ticket === request) {
        status = "failed";
      }
    }
  }

  function filter(nextCountry: string | null, nextPipeline: string | null) {
    country = nextCountry;
    pipeline = nextPipeline;
    if (country == null && pipeline == null) {
      request += 1;
      items = initial.changes;
      next = initial.next;
      status = "idle";
      return;
    }
    void load(true);
  }
</script>

<div class="grid gap-6">
  {#if kinds.length > 0}
    <dl class="flex flex-wrap gap-x-8 gap-y-2">
      {#each kinds as [kind, total] (kind)}
        <div class="grid">
          <dt class="text-[0.8125rem] text-muted-foreground">
            {changeLabel(kind)}
          </dt>
          <dd class="font-figures text-xl font-semibold tabular-nums">
            {count(total)}
          </dd>
        </div>
      {/each}
    </dl>
  {/if}

  <div
    class="grid gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]"
  >
    <div class="grid content-start gap-2">
      <h3 class="text-[0.8125rem] font-semibold">
        {m.downloads_by_country()}
      </h3>
      <ul
        class="grid border-bs text-sm max-lg:overflow-y-auto max-lg:max-block-72"
      >
        {#each countries as row (row.code)}
          <li class="border-be">
            <button
              type="button"
              aria-pressed={country === row.code}
              onclick={() =>
                filter(country === row.code ? null : row.code, null)}
              class={cn(
                "grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 p-1.5 text-start  inline-full hover:bg-secondary focus-visible:outline-2 focus-visible:outline-ring",
                { "bg-secondary font-semibold": country === row.code },
              )}
            >
              <span class="truncate">{row.label}</span>
              <span class="font-figures tabular-nums">{count(row.count)}</span>
              <span
                aria-hidden="true"
                class="col-span-full rounded-[1px] bg-foreground block-0.5"
                style:inline-size="{Math.max(1, (row.count / most) * 100)}%"
              ></span>
            </button>
          </li>
        {/each}
      </ul>
    </div>

    <div class="grid content-start gap-3">
      {#if active != null}
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-[0.9375rem] font-semibold">{active.label}</span>
          <Button
            variant="ghost"
            size="xs"
            onclick={() => filter(null, null)}
            aria-label={m.downloads_changes_clear()}
            ><XIcon />{m.downloads_changes_clear()}</Button
          >
        </div>
        {#if pipelines.length > 1}
          <div class="flex flex-wrap gap-1.5">
            {#each pipelines as [key, total] (key)}
              <Button
                variant={pipeline === key ? "secondary" : "outline"}
                size="xs"
                aria-pressed={pipeline === key}
                onclick={() => filter(country, pipeline === key ? null : key)}
                ><span class="font-figures">{key}</span><span
                  class="font-figures font-normal text-muted-foreground tabular-nums"
                  >{count(total)}</span
                ></Button
              >
            {/each}
          </div>
        {/if}
      {/if}

      {#if items.length === 0 && status !== "loading"}
        <p class="border-bs py-3 text-sm text-muted-foreground">
          {m.downloads_changes_none()}
        </p>
      {:else}
        <ol
          class={cn("grid border-bs text-sm", {
            "opacity-60 transition-opacity": status === "loading",
          })}
          aria-busy={status === "loading"}
        >
          {#each items as change (change.id)}
            <li
              class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-4 border-be py-2 max-sm:grid-cols-1"
            >
              <time
                datetime={change.published_at}
                class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                >{date(change.published_at.slice(0, 10))}</time
              >
              <div class="grid gap-0.5">
                <p
                  class="line-clamp-2 text-pretty wrap-break-word"
                  title={change.summary}
                >
                  {#if change.path == null}
                    {change.summary}
                  {:else}
                    <a
                      href="/{change.path}"
                      class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                      >{change.summary}</a
                    >
                  {/if}
                </p>
                <p
                  class="flex flex-wrap gap-x-3 text-[0.75rem] text-muted-foreground"
                >
                  <span>{changeLabel(change.kind)}</span>
                  {#if change.pipeline != null}
                    <span class="font-figures">{change.pipeline}</span>
                  {/if}
                </p>
              </div>
            </li>
          {/each}
        </ol>
      {/if}

      {#if status === "failed"}
        <p class="text-sm text-destructive" role="alert">
          {m.downloads_changes_failed()}
        </p>
      {/if}
      {#if next != null || status === "failed"}
        <Button
          variant="outline"
          size="sm"
          class="justify-self-start"
          disabled={status === "loading"}
          onclick={async () => load(status === "failed" && isResetting)}
          >{status === "failed"
            ? m.downloads_changes_retry()
            : m.downloads_changes_more()}</Button
        >
      {/if}
    </div>
  </div>
</div>
