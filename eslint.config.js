import antfu, {
  GLOB_HTML,
  GLOB_MARKDOWN,
  GLOB_STYLE,
  GLOB_XML,
  parserPlain,
} from "@antfu/eslint-config";
import pluginQuery from "@tanstack/eslint-plugin-query";
import betterTailwindcss from "eslint-plugin-better-tailwindcss";
import { recommended as inlineUtilities } from "eslint-plugin-inline-utilities";
import path from "node:path";

/**
 * Syntax-only TypeScript rules, safe in `typescript.overrides` (the base,
 * non-type-aware block).
 *
 * @type {import('@antfu/eslint-config').OptionsOverrides['overrides']}
 */
const tsSyntacticOverrides = {
  "ts/array-type": ["error", { default: "generic" }],
  "ts/consistent-indexed-object-style": ["error", "record"],
  "ts/consistent-type-definitions": ["error", "type"],
  "ts/consistent-type-imports": [
    "warn",
    { prefer: "type-imports", fixStyle: "inline-type-imports" },
  ],
  "ts/no-dynamic-delete": "error",
  "ts/no-explicit-any": "error",
  "ts/no-inferrable-types": "error",
};

/**
 * Type-aware TypeScript rules. These must go in
 * `typescript.overridesTypeAware`, otherwise antfu's type-aware block (applied
 * after the base block) silently re-overrides them. See https://github.com/antfu/eslint-config/issues/521.
 *
 * @type {import('@antfu/eslint-config').OptionsOverrides['overrides']}
 */
const tsTypeAwareOverrides = {
  "ts/non-nullable-type-assertion-style": "error",
  "ts/no-array-delete": "error",
  "ts/no-duplicate-type-constituents": "error",
  "ts/no-redundant-type-constituents": "error",
  "ts/no-unnecessary-boolean-literal-compare": "error",
  "ts/no-unnecessary-condition": "error",
  "ts/prefer-includes": "warn",
  "ts/prefer-nullish-coalescing": "error",
  "ts/prefer-optional-chain": "warn",
  "ts/prefer-regexp-exec": "error",
  "ts/prefer-string-starts-ends-with": "error",
  "ts/switch-exhaustiveness-check": [
    "warn",
    { considerDefaultExhaustiveForUnions: true },
  ],
  "ts/use-unknown-in-catch-callback-variable": "error",
};

const sortImports = {
  internalPattern: ["^#lib(/.+)?$"],
  newlinesBetween: 0,
  order: "asc",
  type: "natural",
};

export default antfu({
  stylistic: false,
  markdown: false,
  antislop: true,
  svelte: {
    overrides: {
      "svelte/block-lang": ["error", { script: "ts" }],
      "svelte/button-has-type": "error",
      "svelte/html-closing-bracket-spacing": "error",
      "svelte/html-quotes": ["error", { prefer: "double" }],
      "svelte/html-self-closing": "error",
      "svelte/no-add-event-listener": "error",
      "svelte/no-top-level-browser-globals": "error",
      "svelte/no-useless-mustaches": "off",
      "svelte/prefer-class-directive": "error",
      "svelte/prefer-const": "error",
      "svelte/prefer-derived-over-derived-by": "error",
      "svelte/prefer-style-directive": "error",
      "svelte/valid-compile": "error",
      ...tsSyntacticOverrides,
      ...tsTypeAwareOverrides,
    },
  },
  typescript: {
    tsconfigPath: "tsconfig.json",
    filesTypeAware: ["**/*.?([cm])ts?(x)", "**/*.svelte"],
    parserOptions: { extraFileExtensions: [".svelte"] },
    erasableOnly: true,
    overrides: tsSyntacticOverrides,
    overridesTypeAware: tsTypeAwareOverrides,
  },
  unicorn: {
    allRecommended: true,
    overrides: {
      "unicorn/better-regex": "error",
      "unicorn/filename-case": [
        "error",
        {
          case: "kebabCase",
          ignore: ["README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md"],
        },
      ],
      "unicorn/max-nested-calls": "off",
      "unicorn/name-replacements": "off",
      "unicorn/no-break-in-nested-loop": "off",
      "unicorn/no-nested-ternary": "off",
      "unicorn/no-non-function-verb-prefix": "off",
      "unicorn/no-null": "off",
      "unicorn/no-negated-condition": "off",
      "unicorn/no-top-level-side-effects": "off",
      "unicorn/no-thenable": "off",
      "unicorn/prevent-abbreviations": "off",
      "unicorn/prefer-ternary": "off",
      "unicorn/single-line-block-comment-style": "off",
    },
  },
  imports: {
    overrides: { "perfectionist/sort-imports": ["error", sortImports] },
  },
  rules: {
    "antfu/no-top-level-await": ["off"],
    "jsonc/sort-keys": "off",
    "node/no-process-env": "error",
    "no-console": ["warn"],
    "no-restricted-imports": ["error", { patterns: ["..*"] }],
    "no-unused-vars": "off",
    "pnpm/yaml-enforce-settings": "off",
    "prefer-const": "off",
  },
  ignores: [
    "apps/landing/public/showcase/**/*",
    "apps/landing/showcase/src/**/*",
    "apps/web/src/lib/paraglide/**/*",
    "apps/web/src/lib/components/ui/**/*",
    "apps/web/project.inlang/**/*",
    "packages/db/migrations/**/*",
    "packages/db/src/core.ts",
    "packages/db/src/core-relations.ts",
  ],
})
  .append(pluginQuery.configs["flat/recommended-strict"])
  .append({
    name: "local/plain-text-parser",
    files: [GLOB_STYLE, GLOB_MARKDOWN, GLOB_XML, GLOB_HTML],
    languageOptions: { parser: parserPlain },
  })
  .append(inlineUtilities)
  .append({
    name: "local/sort-imports-web",
    files: ["apps/web/**/*.{js,ts,svelte}"],
    rules: {
      "perfectionist/sort-imports": [
        "error",
        {
          ...sortImports,
          tsconfig: {
            rootDir: path.join(import.meta.dirname, "apps/web"),
            filename: "tsconfig.json",
          },
        },
      ],
    },
  })
  .append({
    name: "local/better-tailwindcss",
    files: ["**/*.svelte", "**/*.?([cm])[jt]s?(x)"],
    plugins: { "better-tailwindcss": betterTailwindcss },
    settings: {
      "better-tailwindcss": {
        entryPoint: "apps/web/src/app.css",
        rootFontSize: 16,
      },
    },
    rules: {
      "better-tailwindcss/enforce-consistent-variant-order": "error",
      "better-tailwindcss/enforce-consistent-variable-syntax": "error",
      "better-tailwindcss/enforce-consistent-important-position": "error",
      "better-tailwindcss/enforce-shorthand-classes": "error",
      "better-tailwindcss/enforce-logical-properties": "error",
      "better-tailwindcss/enforce-canonical-classes": "error",
      "better-tailwindcss/no-deprecated-classes": "error",
      "better-tailwindcss/no-concatenated-classes": "error",
      "better-tailwindcss/no-conflicting-classes": "error",
    },
  });
