<script lang="ts">
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import type { Format } from "#lib/downloads.js";
  import type { ReleaseFile } from "#lib/server/downloads.js";
  import { Input } from "#lib/components/ui/input/index.js";
  import {
    bytes,
    FORMATS,
    groupFiles,
    SHARED_FILES,
    sharedLabel,
    sizes,
  } from "#lib/downloads.js";
  import { fold } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const { calver, files }: { calver: string; files: Array<ReleaseFile> } =
    $props();

  const NAMES: Record<Format, string> = {
    parquet: "Parquet",
    geoparquet: "GeoParquet",
    csv: "CSV",
  };

  let query = $state("");

  const matrix = $derived(sizes(files));
  const columns = $derived(
    FORMATS.filter((format) => matrix.cell("all", format) != null),
  );
  const groups = $derived(groupFiles(files));
  const needle = $derived(fold(query.trim()));
  const shown = $derived(
    needle === ""
      ? groups
      : groups.flatMap((group) => {
          if (fold(group.table).includes(needle)) {
            return [group];
          }
          const items = group.items.filter((item) =>
            [
              item.bundle,
              item.part,
              ...item.parquet.map((file) => file.file),
              item.csv?.file ?? "",
            ].some((text) => fold(text).includes(needle)),
          );
          return items.length === 0 ? [] : [{ ...group, items }];
        }),
  );
  const href = (file: string) => `/downloads/${calver}/${file}`;
</script>

{#snippet cell(bundle: string, format: Format | "all")}
  {const found = $derived(matrix.cell(bundle, format))}
  <td
    class="py-1.5 text-end font-figures tabular-nums"
    class:font-semibold={bundle === "all" || format === "all"}
  >
    {#if found == null}
      <span class="text-muted-foreground">–</span>
    {:else}
      {bytes(found.bytes)}
    {/if}
  </td>
{/snippet}

<div class="grid gap-6">
  <div class="overflow-x-auto">
    <table class="text-sm inline-full min-inline-md">
      <caption class="sr-only">{m.downloads_sizes_caption()}</caption>
      <thead>
        <tr
          class="border-be-2 border-foreground text-[0.75rem] text-muted-foreground"
        >
          <th scope="col" class="pbe-1.5 text-start font-semibold"
            >{m.downloads_col_bundle()}</th
          >
          {#each columns as format (format)}
            <th scope="col" class="pbe-1.5 text-end font-semibold"
              >{NAMES[format]}</th
            >
          {/each}
          <th scope="col" class="pbe-1.5 text-end font-semibold"
            >{m.downloads_col_total()}</th
          >
        </tr>
      </thead>
      <tbody>
        {#each matrix.bundles as bundle (bundle)}
          <tr class="border-be">
            <th scope="row" class="py-1.5 text-start font-figures font-normal"
              >{bundle}</th
            >
            {#each columns as format (format)}
              {@render cell(bundle, format)}
            {/each}
            {@render cell(bundle, "all")}
          </tr>
        {/each}
        <tr>
          <th scope="row" class="py-1.5 text-start font-semibold"
            >{m.downloads_col_total()}</th
          >
          {#each columns as format (format)}
            {@render cell("all", format)}
          {/each}
          {@render cell("all", "all")}
        </tr>
      </tbody>
    </table>
  </div>

  <section class="grid gap-2" aria-labelledby="shared-files">
    <h3 id="shared-files" class="text-[0.9375rem] font-semibold">
      {m.downloads_shared()}
    </h3>
    <ul class="grid border-bs text-sm">
      {#each SHARED_FILES as file (file)}
        <li
          class="grid grid-cols-[minmax(0,13rem)_minmax(0,1fr)] gap-x-4 gap-y-0.5 border-be py-2 max-sm:grid-cols-1"
        >
          <a
            href={href(file)}
            class="inline-flex items-center gap-1.5 justify-self-start font-figures font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
            ><DownloadIcon
              class="shrink-0 text-muted-foreground block-3.5 inline-3.5"
              aria-hidden="true"
            />{file}</a
          >
          <span class="text-pretty text-muted-foreground"
            >{sharedLabel(file)}</span
          >
        </li>
      {/each}
    </ul>
  </section>

  <section class="grid gap-3" aria-labelledby="table-files">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <h3 id="table-files" class="text-[0.9375rem] font-semibold">
        {m.downloads_tables()}
      </h3>
      <label class="grid gap-1 text-[0.75rem] text-muted-foreground">
        {m.downloads_filter()}
        <Input
          type="search"
          bind:value={query}
          placeholder={m.downloads_filter_placeholder()}
          class="inline-64 max-sm:inline-full"
        />
      </label>
    </div>

    {#if shown.length === 0}
      <p class="border-bs py-3 text-sm text-muted-foreground">
        {m.downloads_files_none()}
      </p>
    {:else}
      <div class="grid border-bs">
        {#each shown as group (group.table)}
          <details class="group border-be" open={needle !== ""}>
            <summary
              class="grid cursor-pointer list-none grid-cols-[1rem_minmax(0,1fr)_auto_auto] items-baseline gap-x-4 py-2 text-sm hover:bg-secondary/60 focus-visible:outline-2 focus-visible:outline-ring max-sm:grid-cols-[1rem_minmax(0,1fr)_auto] [&::-webkit-details-marker]:hidden"
            >
              <ChevronRightIcon
                class="self-center text-muted-foreground transition-transform block-3.5 inline-3.5 group-open:rotate-90 motion-reduce:transition-none"
                aria-hidden="true"
              />
              <span class="truncate font-figures font-semibold"
                >{group.table}</span
              >
              <span
                class="text-end font-figures text-muted-foreground tabular-nums max-sm:hidden"
                >{m.downloads_rows({ count: group.rows })}</span
              >
              <span class="text-end font-figures tabular-nums"
                >{bytes(group.bytes)}</span
              >
            </summary>
            <ul class="grid ps-8 pbe-3 text-[0.8125rem]">
              {#each group.items as item (item.key)}
                <li
                  class="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1 border-bs py-1.5 first:border-bs-0 max-sm:grid-cols-[4.5rem_minmax(0,1fr)]"
                >
                  <span class="font-figures text-muted-foreground"
                    >{item.bundle}</span
                  >
                  <span class="grid min-inline-0">
                    <span class="truncate font-figures"
                      >{item.part === "" ? group.table : item.part}</span
                    >
                    <span
                      class="font-figures text-[0.75rem] text-muted-foreground tabular-nums"
                      >{m.downloads_rows({ count: item.rows })}</span
                    >
                  </span>
                  <span
                    class="flex flex-wrap justify-end gap-x-4 gap-y-1 max-sm:col-span-full max-sm:col-start-2 max-sm:justify-start"
                  >
                    {#each item.parquet as file, index (file.file)}
                      <a
                        href={href(file.file)}
                        title="{file.file}, SHA-256 {file.sha256}"
                        class="inline-flex items-baseline gap-1.5 underline decoration-border underline-offset-4 hover:decoration-foreground"
                        ><span class="font-semibold"
                          >{group.table === "geometry"
                            ? NAMES.geoparquet
                            : NAMES.parquet}{item.parquet.length > 1
                            ? ` ${index + 1}/${item.parquet.length}`
                            : ""}</span
                        ><span
                          class="font-figures text-muted-foreground tabular-nums"
                          >{bytes(file.bytes)}</span
                        ></a
                      >
                    {/each}
                    {#if item.csv != null}
                      <a
                        href={href(item.csv.file)}
                        title="{item.csv.file}, SHA-256 {item.csv.sha256}"
                        class="inline-flex items-baseline gap-1.5 underline decoration-border underline-offset-4 hover:decoration-foreground"
                        ><span class="font-semibold">{NAMES.csv}</span><span
                          class="font-figures text-muted-foreground tabular-nums"
                          >{bytes(item.csv.bytes)}</span
                        ></a
                      >
                    {/if}
                  </span>
                </li>
              {/each}
            </ul>
          </details>
        {/each}
      </div>
    {/if}
  </section>
</div>
