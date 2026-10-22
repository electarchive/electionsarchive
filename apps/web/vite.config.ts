import { execFileSync } from "node:child_process";
import { paraglideVitePlugin } from "@inlang/paraglide-js";
import adapter from "@sveltejs/adapter-node";
import { sveltekit } from "@sveltejs/kit/vite";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import devtoolsJson from "vite-plugin-devtools-json";

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
        env: { dir: "../.." },
        compilerOptions: { experimental: { async: true } },
        inspector: { toggleKeyCombo: "control-o" },
      }),
      devtoolsJson(),
      command === "build" &&
        paraglideVitePlugin({
          project: "./project.inlang",
          outdir: "./src/lib/paraglide",
          outputStructure: "message-modules",
          strategy: [
            "cookie",
            "preferredLanguage",
            "globalVariable",
            "baseLocale",
          ],
        }),
    ],
  };
});
