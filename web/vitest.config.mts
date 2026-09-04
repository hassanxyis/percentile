import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * Unit tests for the pure modules in `lib/`.
 *
 * Node environment, not jsdom: the only thing tested here is `roster-csv.ts`,
 * which is text in, result out. Anything needing a DOM or a database belongs in
 * the engine's pytest suite or in a browser check.
 *
 * `.mts` rather than `.ts` — this file uses ESM syntax, and Vite's native config
 * loader warns when it has to load that as CommonJS. `tsconfig.json` already
 * includes `**\/*.mts`.
 *
 * The alias mirrors `tsconfig.json`'s `@/*` so test files import the way
 * application code does.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
