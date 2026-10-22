import "./showcase.css";
import { mount } from "svelte";
import Shell from "./shell.svelte";
import { start } from "./shims/router.svelte.js";

addEventListener(
  "pointerover",
  (event) => {
    const pointer = event.pointerType === "mouse" ? "fine" : "coarse";
    if (document.documentElement.dataset.pointer !== pointer) {
      document.documentElement.dataset.pointer = pointer;
    }
  },
  { capture: true, passive: true },
);

await start();
const target = document.querySelector("#app");
if (target != null) {
  target.replaceChildren();
  mount(Shell, { target });
}
