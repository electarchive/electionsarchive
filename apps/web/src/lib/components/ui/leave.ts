import { beforeNavigate } from "$app/navigation";

export function closeOnLeave(isOpen: () => boolean, close: () => void) {
  beforeNavigate(({ from, to }) => {
    if (isOpen() && from?.url.pathname !== to?.url.pathname) {
      close();
    }
  });
}
