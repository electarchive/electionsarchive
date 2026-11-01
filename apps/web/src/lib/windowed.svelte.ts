import type { VirtualItem, VirtualizerOptions } from "@tanstack/virtual-core";
import type { Attachment } from "svelte/attachments";
import { browser } from "$app/env";
import {
  elementScroll,
  measureElement,
  observeElementOffset,
  observeElementRect,
  observeWindowOffset,
  observeWindowRect,
  Virtualizer,
  windowScroll,
} from "@tanstack/virtual-core";
import { untrack } from "svelte";

type Options<T> = () => {
  items: Array<T>;
  key: (item: T, index: number) => string;
  size: number;
  threshold?: number;
};

type Config = Pick<
  VirtualizerOptions<HTMLElement, HTMLElement>,
  "count" | "estimateSize" | "getItemKey" | "scrollMargin"
>;

type Engine = {
  mount: () => () => void;
  measure: (node: HTMLElement) => void;
  read: (config: Config) => { items: Array<VirtualItem>; total: number };
};

const scrolls = (element: Element) =>
  /auto|scroll/u.test(getComputedStyle(element).overflowY);

const engine = <S extends HTMLElement | Window>(
  virtualizer: Virtualizer<S, HTMLElement>,
): Engine => {
  const queued = new Set<HTMLElement>();
  return {
    mount: () => {
      const stop = virtualizer._didMount();
      virtualizer._willUpdate();
      return stop;
    },
    measure: (node) => {
      if (queued.size === 0) {
        queueMicrotask(() => {
          for (const each of queued) {
            virtualizer.measureElement(each);
          }
          queued.clear();
        });
      }
      queued.add(node);
    },
    read: (config) => {
      virtualizer.setOptions({ ...virtualizer.options, ...config });
      return {
        items: virtualizer.getVirtualItems(),
        total: virtualizer.getTotalSize(),
      };
    },
  };
};

export class Windowed<T> {
  #version = $state(0);
  #margin = $state(0);
  #scroller = $state<HTMLElement | null>(null);
  #options: Options<T>;

  #engine = $derived.by((): Engine => {
    const scroller = this.#scroller;
    const shared = {
      count: 0,
      estimateSize: () => 0,
      measureElement,
      overscan: 12,
      onChange: () => {
        this.#version += 1;
      },
    };
    if (scroller != null) {
      return engine(
        new Virtualizer<HTMLElement, HTMLElement>({
          ...shared,
          getScrollElement: () => scroller,
          observeElementRect,
          observeElementOffset,
          scrollToFn: elementScroll,
          initialRect: { width: 0, height: scroller.clientHeight },
          initialOffset: () => scroller.scrollTop,
        }),
      );
    }
    return engine(
      new Virtualizer<Window, HTMLElement>({
        ...shared,
        getScrollElement: () => (browser ? document.defaultView : null),
        observeElementRect: observeWindowRect,
        observeElementOffset: observeWindowOffset,
        scrollToFn: windowScroll,
        initialRect: { width: 0, height: browser ? window.innerHeight : 1000 },
        initialOffset: () => (browser ? window.scrollY : 0),
      }),
    );
  });

  #config = $derived.by(() => {
    const { items, key, size } = this.#options();
    return {
      count: items.length,
      estimateSize: () => size,
      getItemKey: (index: number) => {
        const item = items[index];
        return item == null ? index : key(item, index);
      },
    };
  });

  readonly isOn = $derived.by(() => {
    const { items, threshold = 200 } = this.#options();
    return items.length > threshold;
  });

  #view = $derived.by(() => {
    const { items, key } = this.#options();
    if (!this.isOn) {
      return {
        before: 0,
        after: 0,
        entries: items.map((value, index) => ({
          value,
          index,
          key: key(value, index),
        })),
      };
    }
    void this.#version;
    const margin = this.#margin;
    const config = { ...this.#config, scrollMargin: margin };
    const { items: slice, total } = this.#engine.read(config);
    const first = slice.at(0);
    const last = slice.at(-1);
    const entries: Array<{ value: T; index: number; key: string }> = [];
    for (const item of slice) {
      const value = items[item.index];
      if (value != null) {
        entries.push({ value, index: item.index, key: key(value, item.index) });
      }
    }
    return {
      before: first == null ? 0 : first.start - margin,
      after: last == null ? 0 : total - (last.end - margin),
      entries,
    };
  });

  constructor(options: Options<T>) {
    this.#options = options;
    $effect(() => {
      if (!this.isOn) {
        return;
      }
      const { mount } = this.#engine;
      return untrack(mount);
    });
  }

  get entries() {
    return this.#view.entries;
  }

  get before() {
    return this.#view.before;
  }

  get after() {
    return this.#view.after;
  }

  readonly anchor: Attachment<HTMLElement> = (node) => {
    const observer = new ResizeObserver(() => {
      update();
    });
    const update = () => {
      let scroller = node.parentElement;
      while (scroller != null && scroller !== document.body) {
        if (
          scrolls(scroller) &&
          scroller.scrollHeight > scroller.clientHeight
        ) {
          break;
        }
        scroller = scroller.parentElement;
      }
      const owner = scroller === document.body ? null : scroller;
      this.#scroller = owner;
      const top = node.getBoundingClientRect().top;
      this.#margin = Math.round(
        owner == null
          ? top + window.scrollY
          : top - owner.getBoundingClientRect().top + owner.scrollTop,
      );
    };
    node.style.overflowAnchor = "none";
    for (
      let element: HTMLElement | null = node;
      element != null;
      element = element.parentElement
    ) {
      observer.observe(element);
    }
    return () => {
      observer.disconnect();
    };
  };

  measure(index: number): Attachment<HTMLElement> {
    return (node) => {
      if (!this.isOn) {
        return;
      }
      node.dataset.index = String(index);
      this.#engine.measure(node);
    };
  }
}
