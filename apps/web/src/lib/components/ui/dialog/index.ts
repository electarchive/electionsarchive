import Footer from "./dialog-footer.svelte";
import Header from "./dialog-header.svelte";
import Overlay from "./dialog-overlay.svelte";
import Portal from "./dialog-portal.svelte";
import Title from "./dialog-title.svelte";
import Trigger from "./dialog-trigger.svelte";

export {

  //

  Header as DialogHeader,
  Overlay as DialogOverlay,
  Portal as DialogPortal,
  Title as DialogTitle,
  Trigger as DialogTrigger,
  Footer,
  Header,
  Overlay,
  Portal,
  Title,
  Trigger,
};

export {
  default as Close,
  default as DialogClose,
} from "./dialog-close.svelte";
export {
  default as Content,
  default as DialogContent,
} from "./dialog-content.svelte";
export {
  default as Description,
  default as DialogDescription,
} from "./dialog-description.svelte";
export { default as Dialog, default as Root } from "./dialog.svelte";
export { default as DialogFooter } from "./dialog-footer.svelte";
