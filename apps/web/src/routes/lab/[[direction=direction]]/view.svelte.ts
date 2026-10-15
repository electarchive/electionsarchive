import { tick } from "svelte";
import { constituencies } from "./election.js";

type Mode = "first" | "second" | "turnout";

export class LabView {
  party = $state<string | null>(null);
  hovered = $state<string | null>(null);
  selected = $state<string | null>("001");
  mode = $state<Mode>("first");
  transitions = $state(true);

  focused = $derived(
    constituencies.find(
      (entry) => entry.code === (this.hovered ?? this.selected),
    ) ?? null,
  );

  transition(update: () => void): void {
    if (
      typeof document !== "undefined" &&
      this.transitions &&
      "startViewTransition" in document &&
      !matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      document.startViewTransition(async () => {
        update();
        await tick();
      });
    } else {
      update();
    }
  }

  setMode(mode: Mode): void {
    this.transition(() => {
      this.mode = mode;
    });
  }

  toggleSelected(code: string): void {
    this.selected = this.selected === code ? null : code;
  }
}
