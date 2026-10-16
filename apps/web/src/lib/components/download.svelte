<script lang="ts">
  import { page } from "$app/state";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import FileJsonIcon from "@lucide/svelte/icons/file-json";
  import FileSpreadsheetIcon from "@lucide/svelte/icons/file-spreadsheet";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    name,
    columns,
    rows,
    class: className,
  }: {
    name: string;
    columns: Array<string>;
    rows: () => Array<Array<string | number | null>>;
    class?: string;
  } = $props();

  let trigger = $state<HTMLElement | null>(null);

  function save(kind: "csv" | "json") {
    const scope = trigger?.closest("section");
    const marked = [
      ...(scope?.querySelectorAll<HTMLElement>("[data-source]") ?? []),
    ];
    const sources = [
      ...new Set(
        (marked.length > 0
          ? marked
          : [...document.querySelectorAll<HTMLElement>("[data-source]")]
        ).flatMap((element) =>
          (
            element.dataset.source ??
            element.textContent.replaceAll(/\s+/g, " ").trim()
          ).split("; "),
        ),
      ),
    ].filter((source) => source !== "");
    const retrieved = new Date().toISOString();
    const url = page.url.href;
    const title = document.title.split(" | ")[0]?.trim() ?? "";
    const data = rows();
    const body =
      kind === "json"
        ? JSON.stringify(
            {
              title,
              table: name,
              url,
              retrieved,
              sources,
              columns,
              rows: data.map((row) =>
                Object.fromEntries(
                  columns.map((column, index) => [column, row[index] ?? null]),
                ),
              ),
            },
            null,
            2,
          )
        : [
            `# ${title}`,
            `# ${m.download_page({ url })}`,
            `# ${m.download_retrieved({ date: retrieved })}`,
            ...(sources.length === 0
              ? []
              : [`# ${m.download_sources({ sources: sources.join("; ") })}`]),
            ...[columns, ...data].map((row) =>
              row
                .map((value) => {
                  const text = value == null ? "" : String(value);
                  return /[",\n#]/.test(text)
                    ? `"${text.replaceAll('"', '""')}"`
                    : text;
                })
                .join(","),
            ),
          ].join("\n");
    const href = URL.createObjectURL(
      new Blob([body], {
        type: kind === "json" ? "application/json" : "text/csv",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${[
      ...page.url.pathname.split("/").filter((part) => part !== ""),
      name,
    ].join("-")}.${kind}`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
</script>

<div class={cn("flex", className)} data-export-skip>
  <DropdownMenu.Root>
    <DropdownMenu.Trigger
      bind:ref={trigger}
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground pointer-coarse:block-10 pointer-coarse:inline-10"
      aria-label={m.download_menu()}
      title={m.download_menu()}
    >
      <DownloadIcon class="block-4 inline-4" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" class="inline-48">
      <DropdownMenu.Item onSelect={() => save("csv")}>
        <FileSpreadsheetIcon class="text-muted-foreground" />{m.download_csv()}
      </DropdownMenu.Item>
      <DropdownMenu.Item onSelect={() => save("json")}>
        <FileJsonIcon class="text-muted-foreground" />{m.download_json()}
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu.Root>
</div>
