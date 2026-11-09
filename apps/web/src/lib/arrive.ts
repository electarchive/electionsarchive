import { invalidate, preloadData } from "$app/navigation";

export async function arrive(
  url: URL,
  go: () => unknown,
  isWanted = () => true,
) {
  const result = await preloadData(url.href);
  const data: Record<string, unknown> =
    result.type === "loaded" ? result.data : {};
  const isReady =
    result.type !== "error" &&
    (data.kind !== "contest" ||
      (await Promise.resolve(data.body)
        .then(() => true)
        .catch(() => false)));
  if (!isReady) {
    await invalidate(() => false);
    return false;
  }
  if (isWanted()) {
    await go();
  }
  return true;
}
