function copyBySelection(text: string) {
  const focused =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true;
  field.style.position = "fixed";
  field.style.insetBlockStart = "0";
  field.style.opacity = "0";
  (focused?.closest("[role=dialog]") ?? document.body).append(field);
  field.select();
  const isCopied = document.execCommand("copy");
  field.remove();
  focused?.focus({ preventScroll: true });
  return isCopied;
}

export async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    if (!copyBySelection(text)) {
      throw error;
    }
  }
}
