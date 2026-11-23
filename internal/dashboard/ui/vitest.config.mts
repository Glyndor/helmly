import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: {
			"@": fileURLToPath(new URL("./src", import.meta.url)),
		},
		conditions: ["import", "module", "browser", "default"],
	},
	test: {
		environment: "jsdom",
		globals: true,
		include: ["src/__tests__/**/*.test.{ts,tsx}"],
		server: {
			deps: {
				inline: ["zod"],
			},
		},
		setupFiles: ["./src/__tests__/setup.ts"],
	},
});
