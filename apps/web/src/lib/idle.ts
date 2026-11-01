export function idle(callback: () => void) {
  if ("requestIdleCallback" in globalThis) {
    const handle = requestIdleCallback(callback, { timeout: 1500 });
    return () => cancelIdleCallback(handle);
  }
  const handle = setTimeout(callback, 300);
  return () => clearTimeout(handle);
}
