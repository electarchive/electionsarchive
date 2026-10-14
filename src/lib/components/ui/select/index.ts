import Label from "./select-label.svelte";
import Portal from "./select-portal.svelte";
import ScrollDownButton from "./select-scroll-down-button.svelte";
import ScrollUpButton from "./select-scroll-up-button.svelte";
import Separator from "./select-separator.svelte";
import Trigger from "./select-trigger.svelte";
import Value from "./select-value.svelte";
import Root from "./select.svelte";

export {
  Portal,
  Root,
  ScrollDownButton,
  ScrollUpButton,
  //
  Root as Select,
  Label as SelectLabel,
  Portal as SelectPortal,
  ScrollDownButton as SelectScrollDownButton,
  ScrollUpButton as SelectScrollUpButton,
  Separator as SelectSeparator,
  Trigger as SelectTrigger,
  Value as SelectValue,
  Separator,
  Trigger,
  Value,
};

export {
  default as Content,
  default as SelectContent,
} from "./select-content.svelte";
export {
  default as Group,
  default as SelectGroup,
} from "./select-group.svelte";
export {
  default as GroupHeading,
  default as SelectGroupHeading,
} from "./select-group-heading.svelte";
export { default as Item, default as SelectItem } from "./select-item.svelte";
export { default as Label } from "./select-label.svelte";
