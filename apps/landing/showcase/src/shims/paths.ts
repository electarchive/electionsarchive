export const base = "";
export const assets = "";

export function resolve(
  route: string,
  params: Record<string, string | undefined> = {},
) {
  return route
    .replaceAll(/\[\.\.\.(\w+)\]/g, (_, name: string) => params[name] ?? "")
    .replaceAll(/\[\[?(\w+)\]?\]/g, (_, name: string) => params[name] ?? "")
    .replaceAll(/\/\([^)]+\)/g, "");
}

export function asset(file: string) {
  return file;
}
