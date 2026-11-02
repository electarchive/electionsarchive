export type Point = { index: number; x: number; y: number };

export function pointAt(event: PointerEvent): Point | null {
  if (event.pointerType !== "mouse" || !(event.target instanceof Element)) {
    return null;
  }
  const found = event.target.closest<HTMLElement>("[data-cell]");
  return found == null
    ? null
    : { index: Number(found.dataset.cell), x: event.clientX, y: event.clientY };
}
