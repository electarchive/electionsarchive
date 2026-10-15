import type { KnipConfig } from "knip";

const config: KnipConfig = {
  workspaces: {
    ".": {},
    "apps/web": {
      entry: ["src/env.ts", "src/hooks.ts", "src/lib/**/*.{svelte,ts}"],
    },
    "packages/db": { entry: ["drizzle.pull.config.ts"] },
  },
  treatConfigHintsAsErrors: true,
};

export default config;
