import type { Attachment } from "svelte/attachments";

export function reveal(selector: string | null): Attachment<HTMLElement> {
  return (node) => {
    if (selector == null) {
      return;
    }
    const frame = requestAnimationFrame(() => {
      const cell = node.querySelector(selector);
      if (cell instanceof HTMLElement) {
        node.scrollLeft = Math.max(
          0,
          cell.offsetLeft + cell.offsetWidth - node.clientWidth,
        );
      }
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  };
}
