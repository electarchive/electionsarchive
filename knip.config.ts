import type { KnipConfig } from "knip";

const config: KnipConfig = {
  entry: ["src/env.ts", "src/hooks.ts", "src/lib/**/*.{svelte,ts}"],
  treatConfigHintsAsErrors: true,
};

export default config;
