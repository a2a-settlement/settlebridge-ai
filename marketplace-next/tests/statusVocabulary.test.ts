// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  fundingLabels,
  repoStatusLabels,
  roleLabels,
  stepLabels,
} from "../lib/statusLabels.ts";
import type { CyberFundingState, RepoStatus, StepState } from "@/lib/cyberContracts";

const STEP_STATES: readonly StepState[] = [
  "done",
  "in_progress",
  "not_started",
  "blocked",
  "inconclusive",
  "not_assessed",
  "needs_decision",
];

const REPO_STATUSES: readonly RepoStatus[] = [
  "needs_attention",
  "task_in_progress",
  "no_open_tasks",
  "paused",
  "setup_needed",
  "not_assessed",
];

const FUNDING_STATES: readonly CyberFundingState[] = [
  "balance_checked",
  "funds_held",
  "payment_pending",
  "payment_review",
  "paid",
  "not_paid",
];

const MONEY_DARK = "text-money-dark";

function tokens(className: string): string[] {
  return className.split(/\s+/).filter(Boolean);
}

function assertCovered(
  name: string,
  actual: Record<string, { label: string; textClass: string }>,
  expected: readonly string[]
) {
  assert.deepEqual(Object.keys(actual).sort(), [...expected].sort(), name);
  for (const key of expected) {
    const entry = actual[key];
    assert.ok(entry, `${name} missing ${key}`);
    assert.equal(typeof entry.label, "string");
    assert.ok(entry.label.trim().length > 0, `${name} ${key} label`);
    assert.ok(entry.textClass.trim().length > 0, `${name} ${key} class`);
  }
}

test("step, repo, and funding labels cover every state", () => {
  assertCovered("step", stepLabels, STEP_STATES);
  assertCovered("repo", repoStatusLabels, REPO_STATUSES);
  assertCovered("funding", fundingLabels, FUNDING_STATES);
});

test("only done and paid use text-money-dark", () => {
  const hits: string[] = [];
  for (const [key, entry] of Object.entries(stepLabels)) {
    if (tokens(entry.textClass).includes(MONEY_DARK)) hits.push(`step:${key}`);
  }
  for (const [key, entry] of Object.entries(repoStatusLabels)) {
    if (tokens(entry.textClass).includes(MONEY_DARK)) hits.push(`repo:${key}`);
  }
  for (const [key, entry] of Object.entries(fundingLabels)) {
    if (tokens(entry.textClass).includes(MONEY_DARK)) hits.push(`funding:${key}`);
  }
  assert.deepEqual(hits.sort(), ["funding:paid", "step:done"]);
});

test("step colors and the not-assessed label match the vocabulary", () => {
  assert.equal(stepLabels.done.textClass, "text-money-dark");
  assert.equal(stepLabels.in_progress.textClass, "text-navy-700");
  assert.equal(stepLabels.not_started.textClass, "text-gray-400");
  assert.equal(stepLabels.blocked.textClass, "text-danger");
  assert.equal(stepLabels.inconclusive.textClass, "text-warning-dark");
  assert.equal(stepLabels.not_assessed.textClass, "text-gray-500");
  assert.equal(stepLabels.needs_decision.textClass, "text-warning-dark");
  assert.equal(stepLabels.not_assessed.label, "Not assessed");
  assert.equal(repoStatusLabels.not_assessed.label, "Not assessed");
  assert.equal(repoStatusLabels.no_open_tasks.textClass, "text-gray-400");
});

test("funding and role labels match the vocabulary", () => {
  assert.equal(fundingLabels.balance_checked.label, "Balance checked — not reserved");
  assert.equal(fundingLabels.funds_held.label, "Reserved — held for active claim");
  assert.equal(fundingLabels.payment_pending.label, "Payment pending");
  assert.equal(fundingLabels.payment_review.label, "Payment under review — funds held");
  assert.equal(fundingLabels.paid.label, "Paid");
  assert.equal(fundingLabels.not_paid.label, "Not paid — refunded or cancelled");
  assert.equal(roleLabels.fund, "Funder / Maintainer");
  assert.equal(roleLabels.solve, "Solver / Challenger");
});
