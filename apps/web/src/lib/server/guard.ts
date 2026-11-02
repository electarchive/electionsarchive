import type { Handle } from "@sveltejs/kit/hooks";
import { building } from "$app/env";
import { promises as dns } from "node:dns";
import { BlockList, isIP } from "node:net";
import { redis } from "#lib/server/redis.js";

type Operator = {
  name: string;
  agent: RegExp;
  lists?: Array<string>;
  hosts?: Array<string>;
};

type Client = { tokens: number; seconds: number; at: number; running: number };

const GOOGLE = "https://developers.google.com/static/search/apis/ipranges/";

const OPERATORS: Array<Operator> = [
  {
    name: "google",
    agent:
      /Googlebot|Google-InspectionTool|GoogleOther|Storebot-Google|AdsBot-Google|Mediapartners-Google|APIs-Google|FeedFetcher-Google|Google-Site-Verification|Google-Read-Aloud/iu,
    lists: [
      `${GOOGLE}googlebot.json`,
      `${GOOGLE}special-crawlers.json`,
      `${GOOGLE}user-triggered-fetchers.json`,
      `${GOOGLE}user-triggered-fetchers-google.json`,
    ],
    hosts: [".googlebot.com", ".google.com", ".googleusercontent.com"],
  },
  {
    name: "bing",
    agent: /bingbot|BingPreview|adidxbot|MicrosoftPreview/iu,
    lists: ["https://www.bing.com/toolbox/bingbot.json"],
    hosts: [".search.msn.com"],
  },
  {
    name: "apple",
    agent: /Applebot/iu,
    lists: ["https://search.developer.apple.com/applebot.json"],
    hosts: [".applebot.apple.com"],
  },
  {
    name: "duckduckgo",
    agent: /DuckDuckBot|DuckAssistBot/iu,
    lists: ["https://duckduckgo.com/duckduckbot.json"],
  },
  {
    name: "yandex",
    agent: /YandexBot|YandexImages|YandexMobileBot|YandexRenderResourcesBot/iu,
    hosts: [".yandex.ru", ".yandex.net", ".yandex.com"],
  },
  { name: "baidu", agent: /Baiduspider/iu, hosts: [".baidu.com", ".baidu.jp"] },
  {
    name: "parallel",
    agent: /ShapBot/iu,
    lists: ["https://docs.parallel.ai/resources/shapbot.json"],
  },
  {
    name: "openai-search",
    agent: /OAI-SearchBot/iu,
    lists: ["https://openai.com/searchbot.json"],
  },
  {
    name: "openai-user",
    agent: /ChatGPT-User/iu,
    lists: ["https://openai.com/chatgpt-user.json"],
  },
  {
    name: "perplexity",
    agent: /PerplexityBot/iu,
    lists: ["https://www.perplexity.com/perplexitybot.json"],
  },
  {
    name: "perplexity-user",
    agent: /Perplexity-User/iu,
    lists: ["https://www.perplexity.com/perplexity-user.json"],
  },
];

const PREVIEW =
  /facebookexternalhit|Twitterbot|Slackbot|LinkedInBot|Discordbot|TelegramBot|redditbot|Pinterestbot|SkypeUriPreview|Embedly|Iframely/iu;

const BOT =
  /(?:^|[\s;(+,])[\w.-]*?(?:bot|spider|crawler|slurp|scrapy|archiver)(?=[\s/;)+,_-]|$)|HeadlessChrome|PhantomJS|Puppeteer|Playwright|Selenium/iu;

const SPARED = /\bCubot\b/u;

const SCRIPT =
  /^(?:python-requests|python-urllib|python-httpx|aiohttp|httpx|Go-http-client|curl|Wget|okhttp|Java\/|Apache-HttpClient|node-fetch|axios|undici|node|libwww-perl|Ruby|PHP|Dart|reqwest|colly|Faraday)/iu;

const FREE =
  /^\/(?:_app\/immutable\/|robots\.txt$|llms\.txt$|sitemap|favicon|\.well-known\/)/u;

const MACHINE = /^\/(?:api\/|mcp(?:\/|$)|rpc\/)|\.(?:md|json)$/u;

const PRIVATE = new BlockList();
PRIVATE.addSubnet("127.0.0.0", 8, "ipv4");
PRIVATE.addSubnet("10.0.0.0", 8, "ipv4");
PRIVATE.addSubnet("172.16.0.0", 12, "ipv4");
PRIVATE.addSubnet("192.168.0.0", 16, "ipv4");
PRIVATE.addSubnet("100.64.0.0", 10, "ipv4");
PRIVATE.addAddress("::1", "ipv6");
PRIVATE.addSubnet("fc00::", 7, "ipv6");

const CRAWLERS = 6;
const EACH = 2;
const PREVIEWS = 3;
const SHED = 24;
const BURST = 240;
const RATE = 4;
const BUDGET = 300;
const EARNED = 2;
const CONCURRENT = 16;
const CHARGED = 30;
const LONGEST = 120_000;
const REMEMBERED = 6 * 3_600_000;
const REMEMBER_LIMIT = 50_000;
const REFRESH = 12 * 3_600_000;
const STORED = "guard:lists:v1";

const FORBIDDEN =
  "This site admits only verified search crawlers. Scripts and agents can read any page as Markdown or JSON by adding .md or .json to its URL.";
const BUSY = "The site is busy. Try again in a minute.";
const LIMITED =
  "Too many requests from your address. Wait a moment and try again.";

const pools = new Map<string, number>();
const clients = new Map<string, Client>();
const verdicts = new Map<string, { isGenuine: boolean; at: number }>();
const ranges = new Map<string, BlockList>();
let crawling = 0;
let inFlight = 0;

function prefixes(body: unknown) {
  const found: Array<string> = [];
  if (typeof body !== "object" || body == null || !("prefixes" in body)) {
    return found;
  }
  const list: unknown = body.prefixes;
  if (!Array.isArray(list)) {
    return found;
  }
  const items: Array<unknown> = list;
  for (const entry of items) {
    if (typeof entry !== "object" || entry == null) {
      continue;
    }
    const fields = new Map<string, unknown>(Object.entries(entry));
    const prefix = fields.get("ipv4Prefix") ?? fields.get("ipv6Prefix");
    if (typeof prefix === "string") {
      found.push(prefix);
    }
  }
  return found;
}

function build(lists: Record<string, Array<string>>) {
  for (const operator of OPERATORS) {
    const block = new BlockList();
    let count = 0;
    for (const url of operator.lists ?? []) {
      for (const prefix of lists[url] ?? []) {
        const [address, bits] = prefix.split("/", 2);
        const family = address == null ? 0 : isIP(address);
        const size = Number(bits);
        if (address != null && family !== 0 && Number.isSafeInteger(size)) {
          block.addSubnet(address, size, family === 4 ? "ipv4" : "ipv6");
          count++;
        }
      }
    }
    if (count > 0) {
      ranges.set(operator.name, block);
    }
  }
  verdicts.clear();
}

async function load() {
  const lists: Record<string, Array<string>> = {};
  try {
    const stored = await redis().get(STORED);
    const parsed: unknown = stored == null ? null : JSON.parse(stored);
    if (typeof parsed === "object" && parsed != null) {
      for (const [url, value] of Object.entries<unknown>({ ...parsed })) {
        if (Array.isArray(value)) {
          lists[url] = value.filter((item) => typeof item === "string");
        }
      }
    }
  } catch {}
  await Promise.all(
    OPERATORS.flatMap((operator) => operator.lists ?? []).map(async (url) => {
      try {
        const response = await fetch(url, {
          signal: AbortSignal.timeout(15_000),
        });
        if (response.ok) {
          const found = prefixes(await response.json());
          if (found.length > 0) {
            lists[url] = found;
          }
        }
      } catch {}
    }),
  );
  build(lists);
  try {
    await redis().set(STORED, JSON.stringify(lists));
  } catch {}
}

function sweep() {
  const now = Date.now();
  for (const [key, client] of clients) {
    if (client.running === 0 && now - client.at > 600_000) {
      clients.delete(key);
    }
  }
}

if (!building) {
  void load();
  setInterval(() => void load(), REFRESH).unref();
  setInterval(sweep, 60_000).unref();
}

async function resolves(address: string, hosts: Array<string>) {
  try {
    const names = await Promise.race([
      dns.reverse(address),
      new Promise<Array<string>>((_, reject) =>
        setTimeout(() => reject(new Error("timeout")), 2000),
      ),
    ]);
    for (const name of names) {
      const host = name.toLowerCase().replace(/\.$/u, "");
      if (hosts.every((suffix) => !host.endsWith(suffix))) {
        continue;
      }
      const forward =
        isIP(address) === 6
          ? await dns.resolve6(host)
          : await dns.resolve4(host);
      if (forward.includes(address)) {
        return true;
      }
    }
  } catch {}
  return false;
}

async function genuine(operator: Operator, address: string, family: 4 | 6) {
  const key = `${operator.name}|${address}`;
  const known = verdicts.get(key);
  if (known != null && Date.now() - known.at < REMEMBERED) {
    return known.isGenuine;
  }
  let isGenuine =
    ranges.get(operator.name)?.check(address, `ipv${family}`) === true;
  if (!isGenuine && operator.hosts != null) {
    isGenuine = await resolves(address, operator.hosts);
  }
  if (verdicts.size >= REMEMBER_LIMIT) {
    verdicts.clear();
  }
  verdicts.set(key, { isGenuine, at: Date.now() });
  return isGenuine;
}

function owner(address: string, family: 4 | 6) {
  if (family === 4) {
    return address;
  }
  const [head = "", tail] = address.split("::", 2);
  const groups = head.split(":");
  const width = tail == null ? groups.length : 4;
  while (groups.length < width) {
    groups.push("0");
  }
  return groups.slice(0, 4).join(":");
}

function refuse(status: 403 | 429 | 503, wait = 60) {
  return new Response(
    status === 403 ? FORBIDDEN : status === 429 ? LIMITED : BUSY,
    {
      status,
      headers: {
        "cache-control": "no-store",
        "content-type": "text/plain; charset=utf-8",
        ...(status !== 403 && { "retry-after": String(wait) }),
        ...(status === 429 && {
          "ratelimit-policy": `"requests";q=${BURST};w=${BURST / RATE}, "time";q=${BUDGET};w=${BUDGET / EARNED}`,
          ratelimit: `"requests";r=0;t=${wait}`,
        }),
      },
    },
  );
}

function track(response: Response, done: () => void) {
  const body = response.body;
  if (body == null) {
    done();
    return response;
  }
  const reader = body.getReader();
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done: isDone, value } = await reader.read();
        if (isDone) {
          done();
          controller.close();
        } else {
          controller.enqueue(value);
        }
      } catch (error) {
        done();
        controller.error(error);
      }
    },
    async cancel(reason) {
      done();
      await reader.cancel(reason);
    },
  });
  return new Response(stream, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

function crawl(pool: string, limit: number) {
  const mine = pools.get(pool) ?? 0;
  if (mine >= limit || crawling >= CRAWLERS || inFlight >= SHED) {
    return null;
  }
  pools.set(pool, mine + 1);
  crawling++;
  return () => {
    crawling--;
    const left = (pools.get(pool) ?? 1) - 1;
    if (left > 0) {
      pools.set(pool, left);
    } else {
      pools.delete(pool);
    }
  };
}

function limit(key: string) {
  const now = Date.now();
  const client = clients.get(key) ?? {
    tokens: BURST,
    seconds: BUDGET,
    at: now,
    running: 0,
  };
  const elapsed = (now - client.at) / 1000;
  client.tokens = Math.min(BURST, client.tokens + elapsed * RATE);
  client.seconds = Math.min(BUDGET, client.seconds + elapsed * EARNED);
  client.at = now;
  clients.set(key, client);
  if (client.running >= CONCURRENT) {
    return 1;
  }
  if (client.tokens < 1) {
    return Math.ceil((1 - client.tokens) / RATE);
  }
  if (client.seconds <= 0) {
    return Math.ceil(-client.seconds / EARNED) + 1;
  }
  client.tokens -= 1;
  client.running++;
  const started = performance.now();
  return () => {
    client.running--;
    client.seconds -= Math.min(CHARGED, (performance.now() - started) / 1000);
  };
}

async function admit(request: Request, pathname: string) {
  const address = request.headers
    .get("x-forwarded-for")
    ?.split(",", 1)[0]
    ?.trim();
  const family = address == null ? 0 : isIP(address);
  if (
    address == null ||
    family === 0 ||
    FREE.test(pathname) ||
    PRIVATE.check(address, family === 4 ? "ipv4" : "ipv6")
  ) {
    return { status: 200, release: null } as const;
  }
  const version = family === 4 ? 4 : 6;
  const agent = request.headers.get("user-agent")?.trim() ?? "";
  const operator = OPERATORS.find((entry) => entry.agent.test(agent));
  if (operator != null) {
    if (!(await genuine(operator, address, version))) {
      return { status: 403 } as const;
    }
    const release = crawl(operator.name, EACH);
    return release == null
      ? ({ status: 503 } as const)
      : ({ status: 200, release } as const);
  }
  const preview = PREVIEW.exec(agent)?.[0];
  if (preview != null) {
    const release = crawl(preview.toLowerCase(), PREVIEWS);
    return release == null
      ? ({ status: 503 } as const)
      : ({ status: 200, release } as const);
  }
  const accept = request.headers.get("accept") ?? "";
  if (
    agent === "" ||
    (BOT.test(agent) && !SPARED.test(agent)) ||
    (SCRIPT.test(agent) &&
      !MACHINE.test(pathname) &&
      !/markdown|json/u.test(accept))
  ) {
    return { status: 403 } as const;
  }
  const outcome = limit(owner(address, version));
  return typeof outcome === "number"
    ? ({ status: 429, wait: outcome } as const)
    : ({ status: 200, release: outcome } as const);
}

export const guardHandle: Handle = async ({ event, resolve }) => {
  const verdict = await admit(event.request, event.url.pathname);
  if (verdict.status === 403 || verdict.status === 503) {
    return refuse(verdict.status);
  }
  if (verdict.status === 429) {
    return refuse(429, verdict.wait);
  }
  if (FREE.test(event.url.pathname)) {
    return resolve(event);
  }
  inFlight++;
  let isDone = false;
  const done = () => {
    if (isDone) {
      return;
    }
    isDone = true;
    clearTimeout(timer);
    inFlight--;
    verdict.release?.();
  };
  const timer = setTimeout(done, LONGEST);
  try {
    return track(await resolve(event), done);
  } catch (error) {
    done();
    throw error;
  }
};
