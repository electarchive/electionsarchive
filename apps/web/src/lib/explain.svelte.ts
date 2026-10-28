import { on } from "svelte/events";

export const explain = $state<{
  isOpen: boolean;
  anchor: HTMLElement | null;
  value:
    | { kind: "term"; label: string; definition: string }
    | { kind: "person"; id: string }
    | { kind: "party"; id: string; color: string | null; within: string | null }
    | null;
}>({ isOpen: false, anchor: null, value: null });

const pressed = { kind: "" };

if (typeof document !== "undefined") {
  on(
    document,
    "pointerdown",
    (event) => {
      pressed.kind = event.pointerType;
    },
    { capture: true, passive: true },
  );
}

export const isTouch = (event: Event) => {
  const kind =
    event instanceof PointerEvent && event.pointerType !== ""
      ? event.pointerType
      : event instanceof MouseEvent && event.detail > 0
        ? pressed.kind
        : "";
  return kind === "touch" || kind === "pen";
};

export function show(event: Event, value: NonNullable<typeof explain.value>) {
  event.preventDefault();
  explain.anchor =
    event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  explain.value = value;
  explain.isOpen = true;
}
