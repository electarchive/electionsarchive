<script lang="ts">
  import { page } from "$app/state";
  import BookOpenIcon from "@lucide/svelte/icons/book-open";
  import CodeIcon from "@lucide/svelte/icons/code";
  import DownloadIcon from "@lucide/svelte/icons/download";
  import ImageIcon from "@lucide/svelte/icons/image";
  import LinkIcon from "@lucide/svelte/icons/link";
  import ShareIcon from "@lucide/svelte/icons/share";
  import { getContext, onDestroy, untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import * as DropdownMenu from "#lib/components/ui/dropdown-menu/index.js";
  import { m } from "#lib/paraglide/messages.js";
  import { provenance } from "#lib/provenance.svelte.js";
  import { cn } from "#lib/utils.js";

  const { name, class: className }: { name: string; class?: string } = $props();

  const PROPERTIES = [
    "fill",
    "fill-opacity",
    "stroke",
    "stroke-width",
    "stroke-opacity",
    "stroke-dasharray",
    "stroke-linecap",
    "opacity",
    "display",
    "visibility",
    "font-family",
    "font-size",
    "font-weight",
    "font-variant-numeric",
    "letter-spacing",
    "text-anchor",
    "dominant-baseline",
  ];
  const CREDIT = 28;

  const claimed = getContext<Set<string>>("figures");
  const figure = untrack(() => {
    let slot = 0;
    while (claimed.has(`${name}:${slot}`)) {
      slot += 1;
    }
    const key = `${name}:${slot}`;
    claimed.add(key);
    onDestroy(() => claimed.delete(key));
    return slot === 0 ? name : `${name}-${slot + 1}`;
  });

  let trigger = $state<HTMLElement | null>(null);
  let chart = $state<SVGSVGElement | null>(null);

  const area = (element: Element) => {
    const box = element.getBoundingClientRect();
    return box.width * box.height;
  };
  const background = (element: Element) => {
    const own = getComputedStyle(element).backgroundColor;
    return own === "rgba(0, 0, 0, 0)"
      ? getComputedStyle(document.body).backgroundColor
      : own;
  };

  function download(url: string, kind: string) {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${[
      ...page.url.pathname.split("/").filter((part) => part !== ""),
      name,
    ].join("-")}.${kind}`;
    anchor.click();
  }

  function vector(source: SVGSVGElement) {
    const copy = source.cloneNode(true) as SVGSVGElement;
    const box = source.getBoundingClientRect();
    const copies = [copy, ...copy.querySelectorAll("*")];
    for (const [index, original] of [
      source,
      ...source.querySelectorAll("*"),
    ].entries()) {
      const target = copies[index];
      if (target instanceof SVGElement) {
        const computed = getComputedStyle(original);
        const parent =
          original === source || original.parentElement == null
            ? null
            : getComputedStyle(original.parentElement);
        for (const property of PROPERTIES) {
          const value = computed.getPropertyValue(property);
          if (
            { opacity: value !== "1", display: value === "none" }[property] ??
            value !== parent?.getPropertyValue(property)
          ) {
            target.style.setProperty(property, value);
          }
        }
        target.removeAttribute("class");
      }
    }
    copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    copy.setAttribute("width", String(Math.round(box.width)));
    copy.setAttribute("height", String(Math.round(box.height)));
    copy.style.setProperty(
      "background-color",
      background(trigger?.closest("[data-export]") ?? source),
    );
    const url = URL.createObjectURL(
      new Blob(
        [
          new XMLSerializer()
            .serializeToString(copy)
            .replaceAll(/(\.\d{2})\d+/g, "$1"),
        ],
        { type: "image/svg+xml" },
      ),
    );
    download(url, "svg");
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function raster() {
    const root = trigger?.closest<HTMLElement>("[data-export]");
    if (root == null) {
      return;
    }
    const { domToPng } = await import("modern-screenshot");
    const styles = getComputedStyle(root);
    const body = getComputedStyle(document.body);
    let skipped = 0;
    for (const child of root.children) {
      if (
        child instanceof HTMLElement &&
        child.dataset.exportSkip != null &&
        getComputedStyle(child).position !== "absolute"
      ) {
        skipped += child.offsetHeight + (Number.parseFloat(styles.rowGap) || 0);
      }
    }
    try {
      download(
        await domToPng(root, {
          scale: 2,
          backgroundColor: background(root),
          height: root.offsetHeight - skipped + CREDIT,
          style: { gridTemplateRows: "none" },
          filter: (child) =>
            !(
              child instanceof Element && child.hasAttribute("data-export-skip")
            ),
          onCloneNode: (clone) => {
            if (
              !(clone instanceof HTMLElement) ||
              clone.dataset.export == null
            ) {
              return;
            }
            const line = document.createElement("div");
            line.textContent = m.export_credit({
              url: `${page.url.host}${page.url.pathname}${page.url.search}`,
            });
            Object.assign(line.style, {
              blockSize: `${CREDIT}px`,
              display: "flex",
              alignItems: "end",
              font: `500 11px ${body.fontFamily}`,
              color: body.color,
              opacity: "0.7",
            });
            clone.append(line);
          },
        }),
        "png",
      );
    } catch {
      toast.error(m.export_failed());
    }
  }
</script>

<div
  class={cn("flex", className)}
  data-export-skip
  data-figure={figure}
  data-embedded={page.url.searchParams.get("embed") === figure ? "" : undefined}
>
  <DropdownMenu.Root
    onOpenChange={(isOpen) => {
      if (isOpen) {
        chart =
          [
            ...(trigger
              ?.closest("[data-export]")
              ?.querySelectorAll<SVGSVGElement>("svg") ?? []),
          ]
            .filter((svg) => svg.getBoundingClientRect().width > 160)
            .toSorted((a, b) => area(b) - area(a))[0] ?? null;
      }
    }}
  >
    <DropdownMenu.Trigger
      bind:ref={trigger}
      class="flex items-center justify-center rounded-sm text-muted-foreground block-7 inline-7 hover:bg-secondary hover:text-foreground"
      aria-label={m.export_menu()}
      title={m.export_menu()}
    >
      <ShareIcon class="block-4 inline-4" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" class="inline-52">
      <DropdownMenu.Item onSelect={() => void raster()}>
        <ImageIcon class="text-muted-foreground" />{m.export_png()}
      </DropdownMenu.Item>
      {#if chart != null}
        {const source = $derived(chart)}
        <DropdownMenu.Item onSelect={() => vector(source)}>
          <DownloadIcon class="text-muted-foreground" />{m.export_svg()}
        </DropdownMenu.Item>
      {/if}
      <DropdownMenu.Separator />
      <DropdownMenu.Item
        onSelect={() =>
          void navigator.clipboard
            .writeText(page.url.href)
            .then(() => toast.success(m.export_copied()))}
      >
        <LinkIcon class="text-muted-foreground" />{m.export_link()}
      </DropdownMenu.Item>
      <DropdownMenu.Item
        onSelect={() => {
          const url = new URL(page.url.href);
          url.searchParams.set("embed", figure);
          const root = trigger?.closest<HTMLElement>("[data-export]");
          const title =
            root?.querySelector("h2, h3")?.textContent.trim() ?? document.title;
          const height = Math.ceil((root?.offsetHeight ?? 480) + 40);
          void navigator.clipboard
            .writeText(
              `<iframe src="${url.href}" title="${title.replaceAll('"', "&quot;")}" loading="lazy" style="inline-size:100%;block-size:${height}px;border:0" data-ea-embed></iframe>\n<script>(()=>{if(window.eaEmbed)return;window.eaEmbed=1;const o=${JSON.stringify(url.origin)};const tone=f=>{for(let e=f.parentElement;e;e=e.parentElement){const c=getComputedStyle(e).backgroundColor.match(/[\\d.]+/g);if(c&&(c[3]===undefined||+c[3]>0))return .2126*c[0]+.7152*c[1]+.0722*c[2]<128?"dark":"light"}return"light"};addEventListener("message",e=>{if(e.origin!==o)return;for(const f of document.querySelectorAll("iframe[data-ea-embed]"))if(f.contentWindow===e.source){if(e.data?.type==="ea:height")f.style.blockSize=e.data.height+"px";if(e.data?.type==="ea:ready")e.source.postMessage({type:"ea:theme",mode:tone(f)},o)}})})()<\/script>`,
            )
            .then(() => toast.success(m.export_embed_copied()));
        }}
      >
        <CodeIcon class="text-muted-foreground" />{m.export_embed()}
      </DropdownMenu.Item>
      <DropdownMenu.Separator />
      <DropdownMenu.Item
        onSelect={() => {
          provenance.anchor = trigger;
          provenance.value = null;
          provenance.isOpen = true;
        }}
      >
        <BookOpenIcon class="text-muted-foreground" />{m.provenance_title()}
      </DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu.Root>
</div>
