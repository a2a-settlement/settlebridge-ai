// Runs with `npm run test:unit` (node --test, built-in type stripping).
// Modules under test may import from "@/..." only with `import type`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { CHECKS_CAPTION } from "../lib/cyberContracts.ts";

test("checks caption never claims security", () => {
  assert.match(CHECKS_CAPTION, /do not show the code is secure/);
});
