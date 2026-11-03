import { paraglideVitePlugin } from "@inlang/paraglide-js";
import adapter from "@sveltejs/adapter-node";
import { sveltekit } from "@sveltejs/kit/vite";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { execFileSync } from "node:child_process";
import { hostname } from "node:os";
import { defineConfig } from "vite";
import devtoolsJson from "vite-plugin-devtools-json";
import { codeVersion } from "./code-version.ts";

export default defineConfig(({ command }) => {
  if (command === "serve") {
    execFileSync("pnpm", ["compile-paraglide"], { stdio: "inherit" });
  }
  return {
    plugins: [
      tailwindcss(),
      sveltekit({
        preprocess: [vitePreprocess()],
        adapter: adapter(),
        csrf: { trustedOrigins: ["*"] },
        env: { dir: "../.." },
        compilerOptions: { experimental: { async: true } },
        inspector: { toggleKeyCombo: "control-o" },
      }),
      devtoolsJson(),
      codeVersion({
        inputs: ["../../pnpm-lock.yaml", "../../packages/db/migrations"],
        procedures: "src/lib/server/rpc",
      }),
      command === "build" &&
        paraglideVitePlugin({
          project: "./project.inlang",
          outdir: "./src/lib/paraglide",
          outputStructure: "message-modules",
          strategy: ["cookie", "custom-edge", "globalVariable", "baseLocale"],
        }),
    ],
    server: { allowedHosts: [".ts.net", hostname().split(".", 1)[0] ?? ""] },
    optimizeDeps: {
      include: [
        "@lucide/svelte/icons/*",
        "@better-auth/api-key/client",
        "maplibre-gl",
        "modern-screenshot",
        "svelte-sonner",
      ],
    },
  };
});
