import type { Header, RangeResponse, Source } from "pmtiles";
import { SharedPromiseCache } from "pmtiles";

export function isAborted(error: unknown) {
  return error instanceof Error && error.name === "AbortError";
}

export class ForgivingCache extends SharedPromiseCache {
  override async getHeader(source: Source) {
    try {
      return await super.getHeader(source);
    } catch (error) {
      this.cache.delete(source.getKey());
      throw error;
    }
  }

  override async getDirectory(
    source: Source,
    offset: number,
    length: number,
    header: Header,
    signal?: AbortSignal,
  ) {
    try {
      return await super.getDirectory(source, offset, length, header, signal);
    } catch (error) {
      if (!isAborted(error)) {
        // Must match the key SharedPromiseCache builds, which keeps a rejected read forever.
        const key = `${source.getKey()}|${header.etag ?? ""}|${offset}|${length}`;
        this.cache.delete(key);
        this.pendingFetches.delete(key);
      }
      throw error;
    }
  }
}

export function sharedReads(source: Source): Source {
  const reads = new Map<
    string,
    { response: Promise<RangeResponse>; waiting: number; end: () => void }
  >();

  const begin = (
    key: string,
    offset: number,
    length: number,
    etag?: string,
  ) => {
    const controller = new AbortController();
    const response = source.getBytes(offset, length, controller.signal, etag);
    const end = () => {
      if (reads.get(key)?.response === response) {
        reads.delete(key);
      }
      controller.abort();
    };
    void response.then(end).catch(end);
    const read = { response, waiting: 0, end };
    reads.set(key, read);
    return read;
  };

  return {
    getKey: () => source.getKey(),
    async getBytes(offset, length, signal, etag) {
      signal?.throwIfAborted();
      const key = `${offset}:${length}`;
      const read = reads.get(key) ?? begin(key, offset, length, etag);
      read.waiting += 1;
      signal?.addEventListener(
        "abort",
        () => {
          read.waiting -= 1;
          if (read.waiting === 0) {
            read.end();
          }
        },
        { once: true },
      );
      const { data, ...response } = await read.response;
      return { ...response, data: data.slice(0, data.byteLength) };
    },
  };
}
