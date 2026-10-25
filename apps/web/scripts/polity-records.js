import { createServer } from "vite";

const server = await createServer({
  appType: "custom",
  logLevel: "error",
  server: { middlewareMode: true, hmr: false, ws: false },
});
try {
  const { rebuild } = await server.ssrLoadModule(
    "/src/lib/server/polity-records.ts",
  );
  console.warn(await rebuild());
} finally {
  const { db } = await server.ssrLoadModule("/src/lib/server/db/index.ts");
  await db.$client.end();
  await server.close();
}
