import { on } from "svelte/events";

export const finder = $state({ isOpen: false, value: "" });

export const editable = $state<{ entity: string | null }>({ entity: null });

export function summon(value = "") {
  const proxy = document.createElement("input");
  proxy.type = "text";
  proxy.tabIndex = -1;
  proxy.setAttribute("aria-hidden", "true");
  proxy.style.cssText =
    "position:fixed;inset-block-start:0;inset-inline-start:0;opacity:0;block-size:1px;inline-size:1px;font-size:16px;pointer-events:none";
  document.body.append(proxy);
  proxy.focus({ preventScroll: true });
  const timer = setTimeout(() => proxy.remove(), 8000);
  on(proxy, "blur", () => {
    clearTimeout(timer);
    proxy.remove();
  });
  finder.value = value;
  finder.isOpen = true;
}
