import { createServer } from "vite";

const server = await createServer({
  appType: "custom",
  logLevel: "error",
  server: { middlewareMode: true, hmr: false, ws: false },
});
process.on("unhandledRejection", (error) => {
  console.error("Unhandled rejection:", error);
});
try {
  const { db: pool } = await server.ssrLoadModule(
    "/src/lib/server/db/index.ts",
  );
  pool.$client.options.connectionTimeoutMillis = Number(
    process.env.PARTY_PAGE_CONNECT_TIMEOUT_MS ?? 300_000,
  );
  const { rebuild } = await server.ssrLoadModule(
    "/src/lib/server/party-pages.ts",
  );
  for (let attempt = 1; ; attempt += 1) {
    try {
      console.warn(await rebuild());
      break;
    } catch (error) {
      if (attempt === 4) {
        throw error;
      }
      console.error(
        `Attempt ${attempt} failed, retrying in ${attempt}m:`,
        error,
      );
      await new Promise((resolve) => setTimeout(resolve, attempt * 60_000));
    }
  }
} finally {
  const { db } = await server.ssrLoadModule("/src/lib/server/db/index.ts");
  await db.$client.end();
  const { redis } = await server.ssrLoadModule("/src/lib/server/redis.ts");
  redis().unref();
  await server.close();
}
