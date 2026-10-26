import { createServer } from "vite";

const server = await createServer({
  appType: "custom",
  logLevel: "error",
  server: { middlewareMode: true, hmr: false, ws: false },
});
try {
  const { rebuildMosaics } = await server.ssrLoadModule(
    "/src/lib/server/rpc/archive.ts",
  );
  console.warn(await rebuildMosaics());
} finally {
  const { db } = await server.ssrLoadModule("/src/lib/server/db/index.ts");
  await db.$client.end();
  await server.close();
}
