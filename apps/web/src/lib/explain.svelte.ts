export const explain = $state<{
  isOpen: boolean;
  anchor: HTMLElement | null;
  value:
    | { kind: "term"; label: string; definition: string }
    | { kind: "person"; id: string }
    | { kind: "party"; id: string; color: string | null; within: string | null }
    | null;
}>({ isOpen: false, anchor: null, value: null });

export const isTouch = (event: Event) =>
  event instanceof PointerEvent &&
  (event.pointerType === "touch" || event.pointerType === "pen");

export function show(event: Event, value: NonNullable<typeof explain.value>) {
  event.preventDefault();
  explain.anchor =
    event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  explain.value = value;
  explain.isOpen = true;
}
