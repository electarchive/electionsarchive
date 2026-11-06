import type { Attachment } from "svelte/attachments";

export const fits: Attachment<HTMLElement> = (node) => {
  const update = () => {
    node.toggleAttribute("data-fits", node.scrollWidth <= node.clientWidth + 1);
  };
  const observer = new ResizeObserver(update);
  observer.observe(node);
  for (const child of node.children) {
    observer.observe(child);
  }
  return () => {
    observer.disconnect();
  };
};
