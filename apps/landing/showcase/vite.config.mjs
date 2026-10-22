import { svelte, vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "../../web");
const shim = (name) => path.join(here, "src/shims", name);

export default defineConfig({
  root: here,
  base: "/showcase/_app/",
  publicDir: false,
  logLevel: "warn",
  plugins: [
    tailwindcss(),
    svelte({
      preprocess: [vitePreprocess()],
      compilerOptions: { experimental: { async: true } },
    }),
  ],
  resolve: {
    alias: [
      { find: /^#lib\/rpc\.js$/, replacement: shim("rpc.ts") },
      { find: /^#lib\//, replacement: `${web}/src/lib/` },
      { find: /^#routes\//, replacement: `${web}/src/routes/` },
      { find: /^\$app\/state$/, replacement: shim("state.svelte.ts") },
      { find: /^\$app\/navigation$/, replacement: shim("navigation.ts") },
      { find: /^\$app\/paths$/, replacement: shim("paths.ts") },
      { find: /^\$app\/env\/public$/, replacement: shim("public.ts") },
      { find: /^\$app\/env$/, replacement: shim("env.ts") },
      { find: /^pmtiles$/, replacement: shim("pmtiles.ts") },
      {
        find: /^pmtiles-real$/,
        replacement: path.join(
          realpathSync(path.join(web, "node_modules/pmtiles")),
          "dist/esm/index.js",
        ),
      },
      {
        find: /^devalue$/,
        replacement: path.join(
          realpathSync(path.join(web, "node_modules/@sveltejs/kit")),
          "../../devalue/index.js",
        ),
      },
    ],
  },
  build: {
    outDir: path.join(here, "../public/showcase/_app"),
    emptyOutDir: true,
    assetsDir: "",
    manifest: true,
    chunkSizeWarningLimit: 4096,
    assetsInlineLimit: 0,
    rollupOptions: { input: path.join(here, "src/main.ts") },
  },
});
