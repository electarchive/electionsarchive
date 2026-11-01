import type { Attachment } from "svelte/attachments";
import { on } from "svelte/events";

export const settle: Attachment<HTMLElement> = (node) => {
  let frame = requestAnimationFrame(() => {
    frame = requestAnimationFrame(() => {
      node.toggleAttribute("data-settled", true);
    });
  });
  return () => {
    cancelAnimationFrame(frame);
  };
};

export function reveal(selector: string | null): Attachment<HTMLElement> {
  return (node) => {
    if (selector == null) {
      return;
    }
    const show = () => {
      const cell = node.querySelector(selector);
      if (cell instanceof HTMLElement) {
        node.scrollLeft = Math.max(
          0,
          cell.offsetLeft + cell.offsetWidth - node.clientWidth,
        );
      }
    };
    let stop = () => {};
    const frame = requestAnimationFrame(() => {
      if (node.checkVisibility({ contentVisibilityAuto: true })) {
        show();
        return;
      }
      stop = on(
        node.parentElement ?? node,
        "contentvisibilityautostatechange",
        (event) => {
          if (
            !(event instanceof ContentVisibilityAutoStateChangeEvent) ||
            event.skipped
          ) {
            return;
          }
          stop();
          show();
        },
      );
    });
    return () => {
      cancelAnimationFrame(frame);
      stop();
    };
  };
}
