import type { KnipConfig } from "knip";

const config: KnipConfig = {
  workspaces: {
    ".": {},
    "apps/web": {
      entry: [
        "src/env.ts",
        "src/hooks.ts",
        "src/lib/**/*.{svelte,ts}",
        "scripts/*.js",
      ],
      ignoreDependencies: [
        "@fontsource/piazzolla",
        "@fontsource/sofia-sans-semi-condensed",
      ],
    },
    "packages/db": { entry: ["drizzle.pull.config.ts"] },
  },
  ignore: [".playwright-mcp/**", "apps/landing/**"],
  treatConfigHintsAsErrors: true,
};

export default config;
