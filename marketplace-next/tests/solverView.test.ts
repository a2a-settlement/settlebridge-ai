// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import { fundingLabels, repoStatusLabels } from "../lib/statusLabels.ts";
import {
  dedupeAvailableTasks,
  deriveSolverRepos,
  formatAte,
  formatUtcTimestamp,
  payoutFundingState,
  summarizeEarnings,
  type EligibleSolverBounty,
  type SolverLeaseInput,
} from "../lib/solverView.ts";
import type { RepoStatus } from "@/lib/cyberContracts";

const NOW = Date.parse("2026-11-07T12:00:00Z");
const OPEN_DEADLINE = "2026-11-07T18:05:00Z";
const CLOSED_DEADLINE = "2026-11-07T11:00:00Z";

function lease(overrides: Partial<SolverLeaseInput> = {}): SolverLeaseInput {
  return {
    lease_id: "lease-1",
    bounty_id: "bounty-1",
    repo: "acme/widgets",
    issue_number: 4,
    status: "work",
    work_expires_at: OPEN_DEADLINE,
    pr_number: null,
    evaluation: null,
    payout: null,
    amount_ate: 10,
    ...overrides,
  };
}

function eligible(
  overrides: Partial<EligibleSolverBounty> = {}
): EligibleSolverBounty {
  return {
    bounty_id: "bounty-1",
    repo: "acme/widgets",
    issue_number: 4,
    title: "Fix the alert",
    amount_ate: 10,
    required_ema: 1.5,
    ...overrides,
  };
}

function repoStatus(leases: SolverLeaseInput[], tasks: Pick<EligibleSolverBounty, "bounty_id" | "repo">[] = []) {
  const cards = deriveSolverRepos(leases, tasks, NOW);
  assert.equal(cards.length, 1);
  return cards[0];
}

function isGreen(className: string): boolean {
  const tokens = className.split(/\s+/);
  return tokens.includes("text-money-dark") || tokens.some((token) => token.includes("green"));
}

test("a claimed lease with an open work window needs attention", () => {
  const card = repoStatus([lease()]);
  assert.equal(card.status, "needs_attention");
  assert.equal(
    card.reason,
    `Open a pull request before ${formatUtcTimestamp(OPEN_DEADLINE)}.`
  );
  assert.equal(card.reason, "Open a pull request before Nov 7, 2026, 18:05 UTC.");
  assert.equal(card.bountyId, "bounty-1");
});

test("the work window is open at the exact deadline and closed after it", () => {
  const atDeadline = repoStatus([lease({ work_expires_at: "2026-11-07T12:00:00Z" })]);
  assert.equal(atDeadline.status, "needs_attention");

  const closed = repoStatus([lease({ work_expires_at: CLOSED_DEADLINE })]);
  assert.equal(closed.status, "no_open_tasks");
  assert.equal(closed.reason, "No active work");
});

test("maintainer review on a lease asks nothing of the solver", () => {
  const submitted = repoStatus([
    lease({
      status: "submitted",
      evaluation: "needs_review",
      work_expires_at: CLOSED_DEADLINE,
      pr_number: 9,
    }),
  ]);
  assert.equal(submitted.status, "task_in_progress");
  assert.equal(submitted.reason, "Waiting for maintainer review");

  const closedWork = repoStatus([
    lease({ status: "work", evaluation: "needs_review", work_expires_at: CLOSED_DEADLINE }),
  ]);
  assert.equal(closedWork.status, "no_open_tasks");
  assert.equal(closedWork.reason, "No active work");
});

test("an open work window outranks a maintainer-review evaluation", () => {
  const card = repoStatus([lease({ evaluation: "needs_review" })]);
  assert.equal(card.status, "needs_attention");
  assert.equal(card.reason, "Open a pull request before Nov 7, 2026, 18:05 UTC.");
});

test("submitted and evaluating leases are task in progress", () => {
  const evaluating = repoStatus([lease({ status: "submitted", evaluation: null })]);
  assert.equal(evaluating.status, "task_in_progress");
  assert.equal(evaluating.reason, "Checks are running");

  const acceptable = repoStatus([
    lease({ status: "submitted", evaluation: "acceptable" }),
  ]);
  assert.equal(acceptable.status, "task_in_progress");
  assert.equal(acceptable.reason, "Pull request submitted");
});

test("attention outranks in-progress work on the same repo", () => {
  const card = repoStatus([
    lease({
      lease_id: "lease-submitted",
      bounty_id: "bounty-submitted",
      status: "submitted",
      evaluation: null,
    }),
    lease({
      lease_id: "lease-work",
      bounty_id: "bounty-work",
      status: "work",
      work_expires_at: OPEN_DEADLINE,
    }),
  ]);
  assert.equal(card.status, "needs_attention");
  assert.equal(card.bountyId, "bounty-work");
});

test("released, expired, and settled leases are no open tasks", () => {
  for (const status of ["released", "expired", "settled"]) {
    const card = repoStatus([lease({ status, evaluation: "needs_review" })]);
    assert.equal(card.status, "no_open_tasks", status);
    assert.equal(card.reason, "No active work");
  }
});

test("an eligible repo without active work is no open tasks", () => {
  const cards = deriveSolverRepos(
    [],
    [eligible({ bounty_id: "bounty-9", repo: "acme/idle" })],
    NOW
  );
  assert.equal(cards.length, 1);
  assert.equal(cards[0].status, "no_open_tasks");
  assert.equal(cards[0].meta, "Eligible to claim");
  assert.equal(cards[0].bountyId, "bounty-9");
});

test("repo names are grouped case-insensitively and unknown data is not assessed", () => {
  const cards = deriveSolverRepos(
    [
      lease({ repo: "Acme/Widgets", status: "settled" }),
      lease({ lease_id: "lease-2", repo: "acme/widgets", status: "mystery" }),
    ],
    [],
    NOW
  );
  assert.equal(cards.length, 1);
  assert.equal(cards[0].repo, "Acme/Widgets");
  assert.equal(cards[0].status, "not_assessed");
  assert.equal(cards[0].reason, "Not assessed");

  const unreadable = repoStatus([lease({ work_expires_at: "not-a-time" })]);
  assert.equal(unreadable.status, "not_assessed");
});

test("available tasks are de-duplicated by bounty id", () => {
  const tasks = dedupeAvailableTasks(
    [
      eligible({ bounty_id: "b1", title: "From setup", amount_ate: 10, required_ema: 1.5 }),
      eligible({ bounty_id: "b1", title: "Duplicate", amount_ate: 99, required_ema: 8 }),
      eligible({
        bounty_id: "b2",
        repo: "acme/other",
        issue_number: 2,
        title: "Second",
        amount_ate: 20,
        required_ema: 0,
      }),
    ],
    [
      {
        bounty_id: " b1 ",
        title: "From cyber",
        repo: "acme/widgets",
        issue_number: 4,
        reward_ate: 99,
        work_state: "available",
        eligibility: { min_reputation: 9, min_kya: 3 },
      },
      {
        bounty_id: "b2",
        title: "Claimed copy",
        work_state: "claimed",
        reward_ate: 1,
        eligibility: { min_reputation: 4, min_kya: 2 },
      },
      {
        bounty_id: "b3",
        title: "Public task",
        repo: "acme/public",
        issue_number: 8,
        reward_ate: 5,
        work_state: "available",
        eligibility: { min_reputation: 4, min_kya: 1 },
      },
      {
        bounty_id: "b3",
        title: "Duplicate cyber",
        work_state: "available",
        reward_ate: 80,
      },
    ]
  );

  assert.deepEqual(
    tasks.map((task) => task.bountyId),
    ["b1", "b2", "b3"]
  );
  assert.equal(tasks[0].title, "From setup");
  assert.equal(tasks[0].amountAte, 10);
  assert.equal(tasks[0].requiredReputation, 1.5);
  assert.equal(tasks[0].requiredKya, 3);
  assert.equal(tasks[1].requiredReputation, 0);
  assert.equal(tasks[1].requiredKya, null);
  assert.equal(tasks[2].amountAte, 5);
  assert.equal(tasks[2].requiredKya, 1);
});

test("earnings count paid payouts only", () => {
  const summary = summarizeEarnings([
    lease({ payout: "paid", amount_ate: 10 }),
    lease({ lease_id: "lease-2", payout: "paid", amount_ate: 5 }),
    lease({ lease_id: "lease-3", payout: "pending", amount_ate: 7 }),
    lease({ lease_id: "lease-4", payout: "pending", amount_ate: 1 }),
    lease({ lease_id: "lease-5", payout: "not_paid", amount_ate: 100 }),
    lease({ lease_id: "lease-6", payout: null, amount_ate: 50 }),
    lease({ lease_id: "lease-7", payout: "paid", amount_ate: Number.NaN }),
  ]);

  assert.deepEqual(summary, { paidAte: 15, pendingAte: 8 });
  assert.equal(formatAte(summary.paidAte), "15 ATE (test)");
  assert.equal(formatAte(summary.pendingAte), "8 ATE (test)");
  assert.equal(summarizeEarnings([]).paidAte, 0);
});

test("ATE (test) formatting does not treat non-numbers as earnings", () => {
  assert.equal(formatAte(0), "0 ATE (test)");
  assert.equal(formatAte(1500), "1500 ATE (test)");
  assert.equal(formatAte(12.5), "12.5 ATE (test)");
  assert.equal(formatAte(Number.POSITIVE_INFINITY), "0 ATE (test)");
  assert.equal(formatAte(Number.NaN), "0 ATE (test)");
});

test("solver repo statuses are never green and paid is the only green payout", () => {
  const cards = deriveSolverRepos(
    [
      lease({ lease_id: "open", status: "work" }),
      lease({ lease_id: "submitted", bounty_id: "b2", repo: "acme/beta", status: "submitted" }),
      lease({ lease_id: "done", bounty_id: "b3", repo: "acme/gamma", status: "settled" }),
      lease({ lease_id: "unknown", bounty_id: "b4", repo: "acme/delta", status: "mystery" }),
    ],
    [eligible({ bounty_id: "b5", repo: "acme/epsilon" })],
    NOW
  );
  const seen = new Set<RepoStatus>(cards.map((card) => card.status));
  assert.deepEqual(
    [...seen].sort(),
    ["needs_attention", "no_open_tasks", "not_assessed", "task_in_progress"]
  );

  for (const status of seen) {
    assert.equal(isGreen(repoStatusLabels[status].textClass), false, status);
  }

  const greenPayouts = (["paid", "pending", "not_paid", null] as const).filter((payout) => {
    const state = payoutFundingState(payout);
    return state != null && isGreen(fundingLabels[state].textClass);
  });
  assert.deepEqual(greenPayouts, ["paid"]);
});
