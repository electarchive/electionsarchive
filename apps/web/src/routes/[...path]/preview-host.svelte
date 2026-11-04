<script lang="ts">
  import { beforeNavigate, preloadData } from "$app/navigation";
  import { onDestroy } from "svelte";
  import type { PageData } from "./$types";

  const OPEN = 450;
  const CLOSE = 120;

  let open = $state(false);
  let anchor = $state<HTMLAnchorElement | null>(null);
  let loaded = $state<{
    href: string;
    data: PageData | null;
    failed: boolean;
  } | null>(null);
  const importPopup = async () => import("./preview-popup.svelte");
  let popup = $state<ReturnType<typeof importPopup> | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let next: HTMLAnchorElement | null = null;

  const later = (
    link: HTMLAnchorElement | null,
    run: () => void,
    delay: number,
  ) => {
    clearTimeout(timer);
    next = link;
    timer = setTimeout(() => {
      next = null;
      run();
    }, delay);
  };
  const settle = () => {
    clearTimeout(timer);
    next = null;
  };
  const fill = (href: string) => {
    if (loaded?.href === href) {
      return;
    }
    loaded = { href, data: null, failed: false };
    preloadData(href)
      .then((result) => {
        loaded = {
          href,
          data: result.type === "loaded" ? (result.data as PageData) : null,
          failed: result.type !== "loaded",
        };
      })
      .catch(() => {
        loaded = { href, data: null, failed: true };
      });
  };
  const show = (link: HTMLAnchorElement) => {
    if (next === link) {
      return;
    }
    if (open && link === anchor) {
      settle();
      return;
    }
    popup ??= importPopup();
    later(
      link,
      () => {
        anchor = link;
        open = true;
        fill(link.getAttribute("href") ?? "");
      },
      open ? CLOSE : OPEN,
    );
  };
  const hide = () => {
    later(
      null,
      () => {
        open = false;
      },
      CLOSE,
    );
  };
  const close = () => {
    settle();
    open = false;
  };
  const linkOf = (target: EventTarget | null) =>
    target instanceof Element
      ? target.closest<HTMLAnchorElement>("a[data-preview]")
      : null;

  beforeNavigate(close);
  onDestroy(settle);

  const current = $derived(
    loaded != null && loaded.href === anchor?.getAttribute("href")
      ? loaded
      : null,
  );
</script>

<svelte:document
  onpointerover={(event: PointerEvent) => {
    const link = linkOf(event.target);
    if (link != null && event.pointerType !== "touch") {
      show(link);
    }
  }}
  onpointerout={(event: PointerEvent) => {
    const link = linkOf(event.target);
    if (
      link != null &&
      !(
        event.relatedTarget instanceof Node &&
        link.contains(event.relatedTarget)
      )
    ) {
      hide();
    }
  }}
  onfocusin={(event: FocusEvent) => {
    const link = linkOf(event.target);
    if (link?.matches(":focus-visible") === true) {
      show(link);
    }
  }}
  onfocusout={(event: FocusEvent) => {
    if (linkOf(event.target) != null) {
      hide();
    }
  }}
  onpointerdown={(event: PointerEvent) => {
    if (linkOf(event.target) != null) {
      close();
    }
  }}
  onkeydown={(event: KeyboardEvent) => {
    if (event.key === "Escape") {
      close();
    }
  }}
/>

{#if popup != null}
  {#await popup then { default: Popup }}
    <Popup
      bind:open
      {anchor}
      data={current?.data ?? null}
      failed={current?.failed ?? false}
      onenter={settle}
      onleave={hide}
    />
  {/await}
{/if}
