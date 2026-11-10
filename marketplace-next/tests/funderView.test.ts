// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { RepoStatus } from "@/lib/cyberContracts";
import {
  approvalsWaiting,
  assessRepo,
  attentionReason,
  buildFunderModel,
  formatAteTest,
  mergeOwned,
  pickBudget,
  readLedgerFunder,
  readLinkedRepos,
  readOpenFindings,
  readSetup,
  REPO_STATUS_ORDER,
  roleIncludesView,
  repoFromTags,
  setupStatusPath,
  sortRepoAssessments,
  type FunderModelInput,
  type RepoFacts,
} from "../lib/funderView.ts";
import { repoStatusLabels } from "../lib/statusLabels.ts";

const REPO_STATUSES: readonly RepoStatus[] = [
  "needs_attention",
  "task_in_progress",
  "no_open_tasks",
  "paused",
  "setup_needed",
  "not_assessed",
];

const GREEN = /green|text-money-dark|bg-money/;

function facts(overrides: Partial<RepoFacts> = {}): RepoFacts {
  return {
    repo: "acme/widgets",
    appInstalled: true,
    linkEnabled: true,
    paused: null,
    tasksKnown: true,
    tasks: [],
    openFindings: null,
    bountyCount: 0,
    alertCount: 0,
    autoMergeAcceptable: false,
    ...overrides,
  };
}

function task(
  workState: string,
  extras: Partial<RepoFacts["tasks"][number]> = {}
): RepoFacts["tasks"][number] {
  return {
    bountyId: extras.bountyId ?? "b1",
    title: extras.title ?? "Fix the check",
    workState,
    openFindings: extras.openFindings ?? null,
    source: extras.source ?? null,
  };
}

function modelInput(overrides: Partial<FunderModelInput> = {}): FunderModelInput {
  return {
    contextRepo: null,
    ownedKnown: true,
    owned: [],
    cyber: [],
    cyberComplete: true,
    setups: [],
    ledgerFreeAte: null,
    ledgerExchangeFailed: false,
    ...overrides,
  };
}

test("repo status mapping covers every RepoStatus", () => {
  const samples: { facts: RepoFacts; status: RepoStatus; reason: string }[] = [
    {
      status: "needs_attention",
      reason: "Needs attention: 1 task needs your review",
      facts: facts({ tasks: [task("needs_maintainer_review")] }),
    },
    {
      status: "needs_attention",
      reason: "Needs attention: 2 tasks need your review",
      facts: facts({
        tasks: [
          task("needs_review", { bountyId: "b1" }),
          task("needs_maintainer_review", { bountyId: "b2" }),
        ],
      }),
    },
    {
      status: "task_in_progress",
      reason: "Task in progress",
      facts: facts({ tasks: [task("claimed")] }),
    },
    {
      status: "paused",
      reason: "Funding for this repository is paused.",
      facts: facts({ paused: true, tasks: [] }),
    },
    {
      status: "setup_needed",
      reason: "The SettleBridge GitHub App is not installed.",
      facts: facts({ appInstalled: false, linkEnabled: null, tasks: [] }),
    },
    {
      status: "setup_needed",
      reason: "The repository link is disabled.",
      facts: facts({ appInstalled: true, linkEnabled: false, tasks: [] }),
    },
    {
      status: "not_assessed",
      reason: "Repository data was not returned.",
      facts: facts({
        appInstalled: null,
        linkEnabled: null,
        paused: null,
        tasksKnown: false,
        tasks: [],
        bountyCount: null,
        alertCount: null,
      }),
    },
    {
      status: "no_open_tasks",
      reason: "No open tasks",
      facts: facts({ tasks: [task("completed")] }),
    },
  ];

  const seen = new Set<RepoStatus>();
  for (const sample of samples) {
    const assessment = assessRepo(sample.facts);
    assert.equal(assessment.status, sample.status);
    assert.equal(assessment.reason, sample.reason);
    seen.add(assessment.status);
    assert.equal(GREEN.test(assessment.reason), false);
    assert.equal(GREEN.test(assessment.meta), false);
    assert.equal(GREEN.test(repoStatusLabels[assessment.status].textClass), false);
  }
  assert.deepEqual([...seen].sort(), [...REPO_STATUSES].sort());
});

test("repo status priority follows attention, in-flight, paused, then setup", () => {
  assert.equal(
    assessRepo(facts({ tasks: [task("needs_maintainer_review"), task("claimed", { bountyId: "b2" })] })).status,
    "needs_attention"
  );
  assert.equal(
    assessRepo(facts({ paused: true, tasks: [task("available")] })).status,
    "task_in_progress"
  );
  assert.equal(
    assessRepo(facts({ paused: true, appInstalled: false, tasks: [] })).status,
    "paused"
  );
  assert.equal(
    assessRepo(facts({ appInstalled: false, tasksKnown: false, tasks: [task("claimed")] })).status,
    "setup_needed"
  );
  assert.equal(assessRepo(facts({ tasks: [task("completed")], paused: null })).status, "no_open_tasks");
});

test("awaiting merge is attention only when auto-merge is off, and findings are not green", () => {
  assert.equal(
    assessRepo(facts({ autoMergeAcceptable: false, tasks: [task("awaiting_merge")] })).status,
    "needs_attention"
  );
  assert.equal(
    assessRepo(facts({ autoMergeAcceptable: true, tasks: [task("awaiting_merge")] })).status,
    "task_in_progress"
  );
  assert.equal(
    assessRepo(facts({ autoMergeAcceptable: null, tasks: [task("awaiting_merge")] })).status,
    "task_in_progress"
  );
  const withFindings = assessRepo(
    facts({ tasks: [task("claimed", { openFindings: 2 })], autoMergeAcceptable: true })
  );
  assert.equal(withFindings.status, "needs_attention");
  assert.equal(withFindings.reason, attentionReason(1));
  assert.equal(
    assessRepo(facts({ tasks: [], openFindings: 3 })).reason,
    "Needs attention: 3 tasks need your review"
  );
  assert.equal(assessRepo(facts({ tasks: [task("claimed", { openFindings: 0 })] })).status, "task_in_progress");
  assert.equal(assessRepo(facts({ tasks: [task("claimed", { openFindings: null })] })).status, "task_in_progress");
});

test("unknown task data and missing counts stay not assessed", () => {
  const unknown = assessRepo(facts({ tasks: [task("mystery")] }));
  assert.equal(unknown.status, "not_assessed");
  const missing = assessRepo(facts({ bountyCount: null, alertCount: null, tasks: [] }));
  assert.equal(missing.status, "no_open_tasks");
  assert.equal(missing.meta, "Bounties not assessed · Alerts not assessed");
  assert.equal(assessRepo(facts({ bountyCount: 1, alertCount: 2, tasks: [task("open")] })).meta, "1 bounty · 2 alerts");
});

test("repo cards sort attention first, then by repository name", () => {
  assert.deepEqual([...REPO_STATUS_ORDER], [
    "needs_attention",
    "setup_needed",
    "task_in_progress",
    "paused",
    "not_assessed",
    "no_open_tasks",
  ]);
  assert.deepEqual([...new Set(REPO_STATUS_ORDER)].sort(), [...REPO_STATUSES].sort());
  const input = [
    { repo: "z/repo", status: "no_open_tasks" as const },
    { repo: "b/repo", status: "needs_attention" as const },
    { repo: "a/repo", status: "needs_attention" as const },
    { repo: "c/repo", status: "paused" as const },
    { repo: "d/repo", status: "setup_needed" as const },
    { repo: "e/repo", status: "task_in_progress" as const },
    { repo: "f/repo", status: "not_assessed" as const },
  ];
  const snapshot = input.map((item) => ({ ...item }));
  const sorted = sortRepoAssessments(input);
  assert.deepEqual(input, snapshot);
  assert.deepEqual(
    sorted.map((item) => item.status),
    [
      "needs_attention",
      "needs_attention",
      "setup_needed",
      "task_in_progress",
      "paused",
      "not_assessed",
      "no_open_tasks",
    ]
  );
  assert.deepEqual(
    sorted.slice(0, 2).map((item) => item.repo),
    ["a/repo", "b/repo"]
  );
});

test("approvals include review states and awaiting merge only when auto-merge is off", () => {
  const rows = approvalsWaiting([
    { bountyId: "review", title: "Review", repo: "acme/a", workState: "needs_maintainer_review", autoMergeAcceptable: true },
    { bountyId: "legacy", title: "Legacy", repo: "acme/a", workState: "needs_review", autoMergeAcceptable: true },
    { bountyId: "wait", title: "Wait", repo: "acme/a", workState: "awaiting_merge", autoMergeAcceptable: false },
    { bountyId: "auto", title: "Auto", repo: "acme/a", workState: "awaiting_merge", autoMergeAcceptable: true },
    { bountyId: "unknown", title: "Unknown", repo: "acme/a", workState: "awaiting_merge", autoMergeAcceptable: null },
    { bountyId: "open", title: "Open", repo: "acme/a", workState: "claimed", autoMergeAcceptable: false },
    { bountyId: "pay", title: "Pay", repo: "acme/a", workState: "payment_review", autoMergeAcceptable: false },
  ]);
  assert.deepEqual(
    rows.map((row) => row.bountyId),
    ["review", "legacy", "wait"]
  );
});

test("ATE (test) formatting does not invent a number", () => {
  assert.equal(formatAteTest(0), "0 ATE (test)");
  assert.equal(formatAteTest(12), "12 ATE (test)");
  assert.equal(formatAteTest(1000), "1,000 ATE (test)");
  assert.equal(formatAteTest(1500), "1,500 ATE (test)");
  assert.equal(formatAteTest(1000000), "1,000,000 ATE (test)");
  assert.equal(formatAteTest(-1000), "-1,000 ATE (test)");
  assert.equal(formatAteTest(Number.NaN), "ATE (test)");
  assert.equal(formatAteTest(Number.POSITIVE_INFINITY), "ATE (test)");
});

test("no repo status uses a green class", () => {
  for (const status of REPO_STATUSES) {
    const tokens = repoStatusLabels[status].textClass.split(/\s+/);
    for (const token of tokens) {
      assert.equal(GREEN.test(token), false, status);
    }
  }
});

test("headroom prefers setup status and ignores a failed exchange balance", () => {
  assert.deepEqual(
    pickBudget({
      setups: [
        { headroomAte: null, minimumAte: 100 },
        { headroomAte: 80, minimumAte: 40 },
      ],
      ledgerFreeAte: 5,
      ledgerExchangeFailed: false,
    }),
    { headroomAte: 80, minimumAte: 40 }
  );
  assert.deepEqual(
    pickBudget({
      setups: [{ headroomAte: null, minimumAte: null }],
      ledgerFreeAte: 0,
      ledgerExchangeFailed: false,
    }),
    { headroomAte: 0, minimumAte: null }
  );
  assert.deepEqual(
    pickBudget({
      setups: [],
      ledgerFreeAte: 15,
      ledgerExchangeFailed: true,
    }),
    { headroomAte: null, minimumAte: null }
  );
  assert.deepEqual(readSetup({ denied: true, snapshot: null }).appInstalled, false);
  assert.equal(readSetup({ denied: true, snapshot: null }).headroomAte, null);
});

test("ledger funder rows and github tags build the owned set", () => {
  const ledger = readLedgerFunder({
    free_ate: 20,
    exchange_error: null,
    entries: [
      {
        role: "funder",
        bounty_id: "b1",
        repo: "acme/widgets",
        issue_number: 4,
        title: "Auth",
        state: "reserved",
        amount_ate: 100,
        fee_ate: 1,
      },
      { role: "solver", bounty_id: "b2", repo: "other/repo", issue_number: 1, title: "Nope", state: "paid", amount_ate: 5, fee_ate: 0 },
    ],
  });
  assert.ok(ledger);
  assert.equal(ledger.rows.length, 1);
  assert.equal(ledger.freeAte, 20);
  assert.equal(ledger.exchangeFailed, false);
  assert.equal(readLedgerFunder({ entries: "nope" }), null);

  const owned = mergeOwned({
    ledgerRows: ledger.rows,
    posted: [
      { id: "b1", title: "Auth", status: "claimed", tags: ["github", "acme/widgets"] },
      { id: "b3", title: "New", status: "open", tags: ["github", "acme/quiet"] },
      { id: "b4", title: "Plain", status: "open", tags: ["writing"] },
    ],
  });
  assert.equal(owned.ownedKnown, true);
  assert.deepEqual(
    owned.owned.map((bounty) => bounty.bountyId).sort(),
    ["b1", "b3"]
  );
  assert.equal(repoFromTags(["GitHub", "acme/widgets"]), "acme/widgets");
  assert.equal(repoFromTags(["writing"]), null);
  assert.equal(mergeOwned({ ledgerRows: null, posted: null }).ownedKnown, false);
  assert.equal(setupStatusPath("acme/widgets"), "/v1/github/repos/acme/widgets/setup-status");
  assert.equal(setupStatusPath("acme/.."), null);
  assert.equal(readOpenFindings({}), null);
  assert.equal(readOpenFindings({ open_findings: 2 }), 2);
  assert.equal(readOpenFindings({ open_findings: 0 }), 0);
  assert.equal(readOpenFindings({ open_findings: -1 }), null);
});

test("the funder model sorts repos, filters approvals, and keeps section facts apart", () => {
  const ready = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "b1",
          title: "Fix auth",
          repo: "acme/widgets",
          bountyStatus: "open",
          ledgerState: "advertised",
        },
        {
          bountyId: "b2",
          title: "Quiet",
          repo: "acme/quiet",
          bountyStatus: "completed",
          ledgerState: "paid",
        },
      ],
      cyber: [
        {
          bountyId: "b1",
          title: "Fix auth",
          repo: "acme/widgets",
          workState: "needs_maintainer_review",
          source: "dependabot",
          openFindings: null,
        },
        {
          bountyId: "b2",
          title: "Quiet",
          repo: "acme/quiet",
          workState: "completed",
          source: "security_label",
          openFindings: null,
        },
      ],
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: false,
            headroomAte: 2500,
            minimumAte: 100,
          },
        },
        {
          repo: "acme/quiet",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: true,
            headroomAte: 2500,
            minimumAte: 100,
          },
        },
      ],
      ledgerFreeAte: 10,
      ledgerExchangeFailed: false,
    })
  );
  assert.deepEqual(
    ready.repos.map((repo) => repo.repo),
    ["acme/widgets", "acme/quiet"]
  );
  assert.equal(ready.repos[0]?.status, "needs_attention");
  assert.equal(ready.repos[0]?.reason, "Needs attention: 1 task needs your review");
  assert.equal(ready.repos[0]?.meta, "1 bounty · 1 alert");
  assert.equal(ready.repos[1]?.status, "no_open_tasks");
  assert.deepEqual(
    ready.approvals.map((item) => item.bountyId),
    ["b1"]
  );
  assert.equal(ready.budget.headroomAte, 2500);
  assert.equal(ready.budget.minimumAte, 100);
  assert.equal(ready.tasksIncomplete, false);

  const merging = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          bountyStatus: null,
          ledgerState: "reserved",
        },
      ],
      cyber: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          workState: "awaiting_merge",
          source: "code_scanning",
          openFindings: null,
        },
      ],
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: false,
            headroomAte: null,
            minimumAte: null,
          },
        },
      ],
    })
  );
  assert.equal(merging.repos[0]?.status, "needs_attention");
  assert.deepEqual(
    merging.approvals.map((item) => item.bountyId),
    ["m1"]
  );

  const auto = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          bountyStatus: null,
          ledgerState: "reserved",
        },
      ],
      cyber: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          workState: "awaiting_merge",
          source: "code_scanning",
          openFindings: 1,
        },
      ],
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: true,
            headroomAte: null,
            minimumAte: null,
          },
        },
      ],
    })
  );
  // Open findings still need a decision even when auto-merge will take the pull request.
  assert.equal(auto.repos[0]?.status, "needs_attention");
  assert.deepEqual(auto.approvals, []);

  const off = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          bountyStatus: null,
          ledgerState: "reserved",
        },
      ],
      cyber: [
        {
          bountyId: "m1",
          title: "Merge me",
          repo: "acme/widgets",
          workState: "awaiting_merge",
          source: null,
          openFindings: null,
        },
      ],
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: true,
            headroomAte: null,
            minimumAte: null,
          },
        },
      ],
    })
  );
  assert.equal(off.repos[0]?.status, "task_in_progress");
  assert.deepEqual(off.approvals, []);

  const truncated = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "c1",
          title: "Done",
          repo: "acme/widgets",
          bountyStatus: "completed",
          ledgerState: "paid",
        },
      ],
      cyber: [
        {
          bountyId: "c1",
          title: "Done",
          repo: "acme/widgets",
          workState: "completed",
          source: "dependabot",
          openFindings: null,
        },
      ],
      cyberComplete: false,
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: false,
            headroomAte: null,
            minimumAte: null,
          },
        },
      ],
    })
  );
  assert.equal(truncated.repos[0]?.status, "not_assessed");
  assert.equal(truncated.tasksIncomplete, true);
  assert.match(truncated.repos[0]?.meta ?? "", /Alerts not assessed/);

  const ledgerOnly = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "r1",
          title: "Held",
          repo: "acme/widgets",
          bountyStatus: null,
          ledgerState: "reserved",
        },
      ],
      cyber: null,
      cyberComplete: false,
      setups: [
        {
          repo: "acme/widgets",
          denied: true,
          snapshot: null,
        },
      ],
      ledgerFreeAte: 44,
      ledgerExchangeFailed: false,
    })
  );
  // A known in-flight hold outranks the missing app. Headroom still falls back to free ATE.
  assert.equal(ledgerOnly.repos[0]?.status, "task_in_progress");
  assert.equal(ledgerOnly.budget.headroomAte, 44);
  assert.equal(ledgerOnly.tasksIncomplete, false);

  const held = buildFunderModel(
    modelInput({
      owned: [
        {
          bountyId: "r1",
          title: "Held",
          repo: "acme/widgets",
          bountyStatus: null,
          ledgerState: "reserved",
        },
      ],
      cyber: null,
      setups: [
        {
          repo: "acme/widgets",
          denied: false,
          snapshot: {
            repoAccess: true,
            enabled: true,
            autoMergeAcceptable: false,
            headroomAte: null,
            minimumAte: null,
          },
        },
      ],
    })
  );
  assert.equal(held.repos[0]?.status, "task_in_progress");
  assert.match(held.repos[0]?.meta ?? "", /Alerts not assessed/);
  assert.deepEqual(held.approvals, []);
});

test("server role, not the header switch, decides which view is allowed", () => {
  assert.equal(roleIncludesView("requester", "fund"), true);
  assert.equal(roleIncludesView("requester", "solve"), false);
  assert.equal(roleIncludesView("agent_operator", "fund"), false);
  assert.equal(roleIncludesView("agent_operator", "solve"), true);
  assert.equal(roleIncludesView("both", "fund"), true);
  assert.equal(roleIncludesView("both", "solve"), true);
  assert.equal(roleIncludesView("other", "fund"), false);
});

test("linked repos appear with no bounties or tasks", () => {
  const model = buildFunderModel(modelInput({ linked: ["widrss/fairsky", "widrss/sentinelrecon"] }));
  assert.deepEqual(
    model.repos.map((repo) => repo.repo).sort(),
    ["widrss/fairsky", "widrss/sentinelrecon"]
  );
  assert.equal(model.discoveryComplete, true);

  const deduped = buildFunderModel(
    modelInput({ linked: ["Acme/Widgets"], contextRepo: "acme/widgets" })
  );
  assert.equal(deduped.repos.length, 1);

  const failed = buildFunderModel(modelInput({ linked: null, contextRepo: "acme/widgets" }));
  assert.equal(failed.repos.length, 1);
  assert.equal(failed.discoveryComplete, false);
});

test("readLinkedRepos keeps valid names and rejects bad payloads", () => {
  assert.deepEqual(
    readLinkedRepos({
      repos: [{ repo: "acme/widgets", enabled: true }, { repo: "not a repo" }, null, { name: "x/y" }],
    }),
    ["acme/widgets"]
  );
  assert.deepEqual(readLinkedRepos({ repos: [] }), []);
  assert.equal(readLinkedRepos({ repos: "acme/widgets" }), null);
  assert.equal(readLinkedRepos(null), null);
});
