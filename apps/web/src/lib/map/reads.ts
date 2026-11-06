import type { RangeResponse, Source } from "pmtiles";

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
