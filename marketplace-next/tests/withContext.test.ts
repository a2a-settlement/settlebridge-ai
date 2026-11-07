// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import { withContext } from "../lib/workContext.ts";
import type { WorkContext } from "@/lib/cyberContracts";

const fund: WorkContext = { view: "fund", repo: null, task: null };
const solve: WorkContext = {
  view: "solve",
  repo: "acme/widgets",
  task: "bounty 1",
};

test("withContext appends view, repo, and task to an internal path", () => {
  assert.equal(
    withContext("/bounties", solve),
    "/bounties?view=solve&repo=acme%2Fwidgets&task=bounty+1"
  );
});

test("withContext keeps unrelated params and overrides view, repo, and task", () => {
  assert.equal(
    withContext("/bounties?page=2&view=fund&sort=new", solve),
    "/bounties?page=2&view=solve&sort=new&repo=acme%2Fwidgets&task=bounty+1"
  );
});

test("withContext drops repo and task when the context clears them", () => {
  assert.equal(
    withContext("/bounties?repo=keep%2Fme&task=old&page=1", fund),
    "/bounties?page=1&view=fund"
  );
});

test("withContext collapses duplicate params", () => {
  assert.equal(withContext("/bounties?view=solve&view=fund", fund), "/bounties?view=fund");
});

test("withContext is idempotent", () => {
  const once = withContext("/bounties?page=2#details", solve);
  assert.equal(withContext(once, solve), once);
});

test("withContext preserves the hash and does not treat it as a query", () => {
  assert.equal(withContext("/docs#install?view=solve", fund), "/docs?view=fund#install?view=solve");
  assert.equal(
    withContext("/docs?x=1#section", solve),
    "/docs?x=1&view=solve&repo=acme%2Fwidgets&task=bounty+1#section"
  );
});

test("withContext preserves an encoded path", () => {
  assert.equal(withContext("/files/a%2Fb", fund), "/files/a%2Fb?view=fund");
});

test("withContext leaves external and protocol-relative URLs unchanged", () => {
  const external = "https://example.com/bounties?view=fund#/internal";
  const protocolRelative = "//cdn.example.com/app?view=solve";
  const mailto = "mailto:ops@example.com?subject=hello";
  assert.equal(withContext(external, solve), external);
  assert.equal(withContext(protocolRelative, solve), protocolRelative);
  assert.equal(withContext(mailto, solve), mailto);
  assert.equal(withContext("bounties", solve), "bounties");
  assert.equal(withContext("#local", solve), "#local");
});
