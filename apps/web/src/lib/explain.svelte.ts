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

const pressed = { kind: "", at: -Infinity };

if (typeof document !== "undefined") {
  for (const type of ["pointerdown", "pointerup"] as const) {
    on(
      document,
      type,
      (event) => {
        if (type === "pointerdown") {
          pressed.kind = event.pointerType;
        }
        if (event.pointerType !== "mouse") {
          pressed.at = event.timeStamp;
        }
      },
      { capture: true, passive: true },
    );
  }
}

export const isTouch = (event: Event) => {
  const kind =
    event.type === "click"
      ? event instanceof MouseEvent && event.detail > 0
        ? pressed.kind
        : ""
      : event instanceof PointerEvent
        ? event.pointerType
        : "";
  return kind === "touch" || kind === "pen";
};

export const isHover = (event: MouseEvent) =>
  (!(event instanceof PointerEvent) || event.pointerType === "mouse") &&
  event.timeStamp - pressed.at > 1000;

export function show(event: Event, value: NonNullable<typeof explain.value>) {
  event.preventDefault();
  explain.anchor =
    event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  explain.value = value;
  explain.isOpen = true;
}
