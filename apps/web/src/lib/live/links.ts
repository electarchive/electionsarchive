import { resolve } from "$app/paths";
import { getContext, setContext } from "svelte";

const MOCK = Symbol("live-mock");

export function mockLinks() {
  setContext(MOCK, true);
}

export function liveLinks() {
  const isMock = getContext<boolean | undefined>(MOCK) === true;
  return (path: string) =>
    resolve(isMock ? "/live/mock/[...path]" : "/live/[...path]", { path });
}
