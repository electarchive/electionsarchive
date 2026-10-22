import * as real from "pmtiles-real";
import { slug } from "./router.svelte.js";

export * from "pmtiles-real";

class Whole {
  private bytes: Promise<ArrayBuffer> | null = null;

  constructor(private readonly url: string) {}

  getKey() {
    return this.url;
  }

  async getBytes(offset: number, length: number) {
    this.bytes ??= fetch(
      this.url.replace(
        `${location.origin}/tiles/`,
        `${location.origin}/showcase/${slug}/tiles/`,
      ),
    ).then((response) => {
      if (!response.ok) {
        throw new Error(`${response.status} ${this.url}`);
      }
      return response.arrayBuffer();
    });
    const bytes = await this.bytes;
    return { data: bytes.slice(offset, offset + length) };
  }
}

export class PMTiles extends real.PMTiles {
  constructor(source: string | real.Source, ...rest: Array<never>) {
    super(typeof source === "string" ? new Whole(source) : source, ...rest);
  }
}

class Archives extends Map<string, real.PMTiles> {
  override get(key: string) {
    if (!this.has(key)) {
      this.set(key, new PMTiles(key));
    }
    return super.get(key);
  }
}

const archives = new Archives();

export class Protocol extends real.Protocol {
  constructor(...options: ConstructorParameters<typeof real.Protocol>) {
    super(...options);
    this.tiles = archives;
  }
}
