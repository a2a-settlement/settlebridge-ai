// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { CHECKS_CAPTION } from "../lib/cyberContracts.ts";
import type {
  ChallengeDetail,
  FailureClass,
  Finding,
  FindingStatus,
  StepState,
  VerdictKind,
} from "../lib/cyberContracts.ts";
import {
  buildChallengeView,
  CHALLENGE_ICON_NAMES,
  FAILURE_CLASS_VIEWS,
  FINDING_STATUS_VIEWS,
  NO_FAILURE_FOUND_TEXT,
  PLATFORM_OPERATED_LABEL,
  safeHref,
  severityPresentation,
  shortSha,
  sortFindings,
  STEP_STATE_ICONS,
  TASK_LABELS,
  VERDICT_VIEWS,
  type ChallengeView,
  type LabelledState,
  type RoleRowView,
} from "../lib/challengeView.ts";

const VERDICT_KINDS = [
  "confirmed",
  "no_failure_found",
  "inconclusive",
  "invalid_or_duplicate",
] as const satisfies readonly VerdictKind[];

const FINDING_STATUSES = [
  "needs_decision",
  "confirmed",
  "inconclusive",
  "dismissed",
  "superseded",
] as const satisfies readonly FindingStatus[];

const FAILURE_CLASSES = [
  "authorization_confidentiality",
  "integrity",
  "injection_execution",
  "denial_of_service",
  "regression",
] as const satisfies readonly FailureClass[];

const TASK_STATES = [
  "draft",
  "frozen",
  "baselining",
  "open",
  "attempting",
  "validating",
  "adjudicating",
  "closed",
  "inconclusive",
  "cancelled",
] as const satisfies readonly ChallengeDetail["state"][];

const STEP_STATES = new Set<StepState>([
  "done",
  "in_progress",
  "not_started",
  "blocked",
  "inconclusive",
  "not_assessed",
  "needs_decision",
]);

const SHA = "0123456789abcdef0123456789abcdef01234567";

function assertNoSecure(value: unknown, label: string) {
  const text = JSON.stringify(value) ?? "";
  const stripped = text.split(CHECKS_CAPTION).join("");
  assert.equal(stripped.toLowerCase().includes("secure"), false, label);
}

function assertMapped(
  name: string,
  views: Record<string, LabelledState>,
  keys: readonly string[]
) {
  assert.deepEqual(Object.keys(views).sort(), [...keys].sort(), name);
  for (const key of keys) {
    const entry = views[key];
    assert.ok(entry, `${name} ${key}`);
    assert.equal(typeof entry.label, "string");
    assert.ok(entry.label.trim().length > 0, `${name} ${key} label`);
    assert.equal(STEP_STATES.has(entry.state), true, `${name} ${key} state`);
  }
}

function makeFinding(overrides: Partial<Finding> & Pick<Finding, "id" | "status">): Finding {
  return {
    kind: "challenge_gap",
    repo: "acme/widgets",
    failure_class: "integrity",
    title: overrides.id,
    advisory_severity: null,
    oracle_citation: null,
    target_sha: SHA,
    evidence_url: "https://example.com/evidence",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeChallenge(overrides: Partial<ChallengeDetail> = {}): ChallengeDetail {
  const base: ChallengeDetail = {
    id: "chal-1",
    repo: "acme/widgets",
    pr_number: 12,
    base_sha: SHA,
    target_sha: SHA,
    state: "validating",
    state_reason: null,
    property_text: "The parser rejects an empty name.",
    participants: [
      { role: "author", operator_label: "Author", platform_operated: false },
      { role: "solver", operator_label: "Ada", platform_operated: false },
      { role: "challenger", operator_label: "platform:challenger", platform_operated: true },
      { role: "verifier", operator_label: "platform:verifier", platform_operated: true },
    ],
    summary: {
      claims_submitted: 0,
      confirmed: 0,
      inconclusive: 0,
      invalid: 0,
      no_failure_found: false,
    },
    verdicts: [],
    findings: [],
    report_url: null,
    scope_note: "Under the stated property, environment, and budget.",
  };
  return {
    ...base,
    ...overrides,
    summary: { ...base.summary, ...overrides.summary },
    participants: overrides.participants ?? base.participants,
    findings: overrides.findings ?? base.findings,
    verdicts: overrides.verdicts ?? base.verdicts,
  };
}

function row(view: ChallengeView, role: RoleRowView["role"]): RoleRowView {
  const match = view.rows.find((item) => item.role === role);
  assert.ok(match, role);
  return match;
}

test("verdict, finding status, and failure class map to a label and state", () => {
  assertMapped("verdict", VERDICT_VIEWS, VERDICT_KINDS);
  assertMapped("finding", FINDING_STATUS_VIEWS, FINDING_STATUSES);
  assertMapped("failure class", FAILURE_CLASS_VIEWS, FAILURE_CLASSES);
  assert.deepEqual(Object.keys(TASK_LABELS).sort(), [...TASK_STATES].sort());
  for (const state of TASK_STATES) {
    assert.ok(TASK_LABELS[state].trim().length > 0);
  }
});

test("status and failure-class labels stay in plain words", () => {
  assert.equal(VERDICT_VIEWS.confirmed.label, "Confirmed");
  assert.equal(VERDICT_VIEWS.confirmed.state, "needs_decision");
  assert.equal(VERDICT_VIEWS.no_failure_found.label, NO_FAILURE_FOUND_TEXT);
  assert.equal(VERDICT_VIEWS.no_failure_found.state, "not_started");
  assert.equal(VERDICT_VIEWS.inconclusive.label, "Inconclusive");
  assert.equal(VERDICT_VIEWS.inconclusive.state, "inconclusive");
  assert.equal(VERDICT_VIEWS.invalid_or_duplicate.label, "Invalid/Dismissed");
  assert.equal(VERDICT_VIEWS.invalid_or_duplicate.state, "blocked");

  assert.equal(FINDING_STATUS_VIEWS.needs_decision.label, "Needs a decision");
  assert.equal(FINDING_STATUS_VIEWS.needs_decision.state, "needs_decision");
  assert.equal(FINDING_STATUS_VIEWS.confirmed.label, "Confirmed");
  assert.notEqual(FINDING_STATUS_VIEWS.confirmed.state, "done");
  assert.equal(FINDING_STATUS_VIEWS.inconclusive.label, "Inconclusive");
  assert.equal(FINDING_STATUS_VIEWS.dismissed.label, "Invalid/Dismissed");
  assert.equal(FINDING_STATUS_VIEWS.superseded.label, "Superseded");

  assert.equal(FAILURE_CLASS_VIEWS.authorization_confidentiality.label, "Authorization or confidentiality");
  assert.equal(FAILURE_CLASS_VIEWS.integrity.label, "Integrity");
  assert.equal(FAILURE_CLASS_VIEWS.injection_execution.label, "Injection or code execution");
  assert.equal(FAILURE_CLASS_VIEWS.denial_of_service.label, "Denial of service");
  assert.equal(FAILURE_CLASS_VIEWS.regression.label, "Regression");
  for (const failureClass of FAILURE_CLASSES) {
    assert.notEqual(FAILURE_CLASS_VIEWS[failureClass].state, "done");
  }
});

test("no_failure_found never maps to done", () => {
  assert.notEqual(VERDICT_VIEWS.no_failure_found.state, "done");
  for (const state of TASK_STATES) {
    const view = buildChallengeView(
      makeChallenge({
        state,
        summary: {
          claims_submitted: 3,
          confirmed: 0,
          inconclusive: 0,
          invalid: 0,
          no_failure_found: true,
        },
      })
    );
    assert.equal(row(view, "challenger").state, "not_started");
    assert.equal(row(view, "challenger").text, NO_FAILURE_FOUND_TEXT);
    assert.notEqual(row(view, "challenger").icon, "CheckCircle2");
    assert.notEqual(row(view, "challenger").icon, STEP_STATE_ICONS.done);
    assert.notEqual(row(view, "verifier").state, "done");
    assert.notEqual(view.taskState, "done");
    assert.equal(row(view, "solver").state, "done");
    assert.equal(row(view, "solver").icon, "Wrench");
  }
});

test("severity is quoted from an advisory or left unrated", () => {
  const unrated = severityPresentation(null);
  assert.equal(unrated.label, "Severity not rated");
  assert.equal(unrated.rated, false);
  assert.equal(unrated.icon, "CircleDashed");

  const cited = severityPresentation({ level: "high", source: "GHSA" });
  assert.equal(cited.label, "High (GHSA)");
  assert.equal(cited.rated, true);
  assert.equal(cited.icon, "BadgeAlert");
  assert.equal(severityPresentation({ level: "critical", source: "CVE-2024-1" }).label, "Critical (CVE-2024-1)");
  assert.equal(severityPresentation({ level: "low", source: "   " }).label, "Low");
});

test("confirmed findings that need a decision sort first", () => {
  const input = [
    makeFinding({ id: "dismissed", status: "dismissed", created_at: "2026-01-01T00:00:00Z" }),
    makeFinding({ id: "upheld", status: "confirmed", created_at: "2026-01-02T00:00:00Z" }),
    makeFinding({ id: "open-late", status: "needs_decision", created_at: "2026-01-04T00:00:00Z" }),
    makeFinding({ id: "superseded", status: "superseded", created_at: "2026-01-01T00:00:00Z" }),
    makeFinding({ id: "unsure", status: "inconclusive", created_at: "2026-01-01T00:00:00Z" }),
    makeFinding({ id: "open-early", status: "needs_decision", created_at: "2026-01-03T00:00:00Z" }),
  ];
  const original = input.map((finding) => finding.id);
  const sorted = sortFindings(input).map((finding) => finding.id);
  assert.deepEqual(sorted, ["open-early", "open-late", "upheld", "unsure", "dismissed", "superseded"]);
  assert.deepEqual(input.map((finding) => finding.id), original);

  const view = buildChallengeView(makeChallenge({ findings: input }));
  assert.deepEqual(
    view.findings.map((finding) => finding.id),
    sorted
  );
});

test("role rows name only that operator and use the verifier icon rule", () => {
  for (const state of TASK_STATES) {
    for (const noFailure of [false, true]) {
      for (const confirmed of [0, 2]) {
        const view = buildChallengeView(
          makeChallenge({
            state,
            state_reason:
              state === "cancelled" || state === "inconclusive" ? "Stopped for the recorded reason" : null,
            summary: {
              claims_submitted: 4,
              confirmed,
              inconclusive: confirmed === 0 && state === "inconclusive" ? 1 : 0,
              invalid: 1,
              no_failure_found: noFailure,
            },
          })
        );
        assert.deepEqual(
          view.rows.map((item) => item.role),
          ["solver", "challenger", "verifier"]
        );
        const solver = row(view, "solver");
        const challenger = row(view, "challenger");
        const verifier = row(view, "verifier");
        assert.equal(solver.icon, "Wrench");
        assert.equal(solver.operatorLabel, "Ada");
        assert.equal(solver.platformLabel, null);
        assert.equal(solver.text, "Patch submitted at 0123456");
        assert.equal(challenger.icon, "ScanSearch");
        assert.equal(challenger.platformLabel, PLATFORM_OPERATED_LABEL);
        assert.equal(challenger.platformOperated, true);
        assert.notEqual(view.taskState, "done");
        assert.notEqual(verifier.state, "done");
        if (confirmed >= 1) {
          assert.equal(verifier.icon, "ShieldCheck");
          assert.notEqual(verifier.state, "done");
        } else {
          assert.equal(verifier.icon, STEP_STATE_ICONS[verifier.state]);
          assert.notEqual(verifier.icon, "ShieldCheck");
        }
        if (noFailure) {
          assert.equal(challenger.state, "not_started");
          assert.equal(challenger.text, NO_FAILURE_FOUND_TEXT);
          assert.notEqual(challenger.state, "done");
        } else {
          assert.equal(challenger.text, "Tried to make the patch fail: 4 claims submitted");
        }
        assert.equal(
          verifier.text,
          `Independently checked ${confirmed + (confirmed === 0 && state === "inconclusive" ? 1 : 0) + 1} claims: ${confirmed} confirmed, ${confirmed === 0 && state === "inconclusive" ? 1 : 0} inconclusive, 1 invalid`
        );
        assertNoSecure(view, `${state}/${noFailure}/${confirmed}`);
      }
    }
  }
});

test("checked counts are the verdict buckets, and reasons show for blocked or inconclusive", () => {
  const view = buildChallengeView(
    makeChallenge({
      state: "closed",
      summary: {
        claims_submitted: 5,
        confirmed: 1,
        inconclusive: 1,
        invalid: 0,
        no_failure_found: false,
      },
    })
  );
  assert.equal(
    row(view, "verifier").text,
    "Independently checked 2 claims: 1 confirmed, 1 inconclusive, 0 invalid"
  );
  assert.equal(row(view, "verifier").icon, "ShieldCheck");

  const cancelled = buildChallengeView(
    makeChallenge({ state: "cancelled", state_reason: "Budget paused" })
  );
  assert.equal(cancelled.taskLabel, "Cancelled");
  assert.equal(cancelled.taskState, "blocked");
  assert.equal(cancelled.taskReason, "Budget paused");
  assert.equal(row(cancelled, "verifier").state, "blocked");
  assert.equal(row(cancelled, "verifier").icon, "Ban");
  assert.equal(row(cancelled, "verifier").reason, "Budget paused");

  const unsure = buildChallengeView(
    makeChallenge({ state: "inconclusive", state_reason: "Positive control failed" })
  );
  assert.equal(unsure.taskState, "inconclusive");
  assert.equal(unsure.taskReason, "Positive control failed");
  assert.equal(row(unsure, "verifier").icon, "CircleHelp");
  assert.equal(row(unsure, "verifier").reason, "Positive control failed");
});

test("missing operators are named as not recorded", () => {
  const view = buildChallengeView(makeChallenge({ participants: [] }));
  for (const item of view.rows) {
    assert.equal(item.operatorLabel, "Not recorded");
    assert.equal(item.platformOperated, false);
    assert.equal(item.platformLabel, null);
  }
  const blank = buildChallengeView(
    makeChallenge({
      participants: [{ role: "solver", operator_label: "   ", platform_operated: false }],
    })
  );
  assert.equal(row(blank, "solver").operatorLabel, "Not recorded");
});

test("report and evidence links stay on usable URLs", () => {
  const linked = buildChallengeView(
    makeChallenge({ report_url: "/api/v1/challenges/chal-1/report" })
  );
  assert.equal(linked.reportHref, "/api/v1/challenges/chal-1/report");
  assert.equal(linked.reportLabel, "View signed report →");

  const hidden = buildChallengeView(makeChallenge({ report_url: null }));
  assert.equal(hidden.reportHref, null);
  assert.equal(hidden.reportLabel, null);

  const rejected = buildChallengeView(makeChallenge({ report_url: "javascript:alert(1)" }));
  assert.equal(rejected.reportHref, null);
  assert.equal(rejected.reportLabel, "Signed report link is not a usable URL");
  assert.equal(safeHref("https://example.com/report"), "https://example.com/report");
  assert.equal(safeHref("data:text/plain,hi"), null);

  const finding = makeFinding({
    id: "gap",
    status: "needs_decision",
    title: "Empty name is accepted",
    failure_class: "integrity",
    advisory_severity: { level: "medium", source: "GHSA" },
    oracle_citation: {
      source: "property_clause",
      reference: "clause 2",
      quote: "Names must not be empty.",
    },
    evidence_url: "https://example.com/evidence/gap",
  });
  const [card] = buildChallengeView(makeChallenge({ findings: [finding] })).findings;
  assert.ok(card);
  assert.equal(card.severityLabel, "Medium (GHSA)");
  assert.equal(card.failureClassLabel, "Integrity");
  assert.equal(card.statusLabel, "Needs a decision");
  assert.equal(card.citation?.sourceLabel, "Property clause");
  assert.equal(card.citation?.reference, "clause 2");
  assert.equal(card.citation?.quote, "Names must not be empty.");
  assert.equal(card.evidenceHref, "https://example.com/evidence/gap");
  assert.equal(card.evidenceLabel, "View evidence →");
  assert.equal(card.kindLabel, "Challenge gap");
});

test("mapper copy does not say secure, aside from the checks caption", () => {
  assert.match(CHECKS_CAPTION, /secure/i);
  assertNoSecure(VERDICT_VIEWS, "verdicts");
  assertNoSecure(FINDING_STATUS_VIEWS, "findings");
  assertNoSecure(FAILURE_CLASS_VIEWS, "classes");
  assertNoSecure(TASK_LABELS, "tasks");
  assertNoSecure(NO_FAILURE_FOUND_TEXT, "no failure text");
  assertNoSecure(PLATFORM_OPERATED_LABEL, "platform");
  assertNoSecure(severityPresentation(null), "unrated");
  assertNoSecure(severityPresentation({ level: "high", source: "GHSA" }), "cited");
  const view = buildChallengeView(makeChallenge());
  assert.equal(JSON.stringify(view).includes(CHECKS_CAPTION), false);
  assertNoSecure(view, "view");
});

function lucideExports(dts: string, name: string): boolean {
  // Lucide 1.x keeps older names as export aliases (CircleAlert as AlertCircle).
  return dts.includes(`declare const ${name}:`) || dts.includes(`as ${name},`) || dts.includes(`as ${name}}`);
}

test("challenge icons are exported by the installed lucide types", () => {
  const dtsPath = join(
    dirname(fileURLToPath(import.meta.url)),
    "../node_modules/lucide-react/dist/lucide-react.d.ts"
  );
  const dts = readFileSync(dtsPath, "utf8");
  const declared = new Set<string>(CHALLENGE_ICON_NAMES);
  for (const name of Object.values(STEP_STATE_ICONS)) {
    assert.equal(declared.has(name), true, name);
  }
  for (const name of ["Wrench", "ScanSearch", "ShieldCheck", "CircleDashed", "BadgeAlert"] as const) {
    assert.equal(declared.has(name), true, name);
  }
  for (const name of CHALLENGE_ICON_NAMES) {
    assert.equal(lucideExports(dts, name), true, name);
  }
});

test("short revisions keep the leading characters", () => {
  assert.equal(shortSha(SHA), "0123456");
  assert.equal(shortSha("abc"), "abc");
  assert.equal(shortSha("  abcdefghij  "), "abcdefg");
});
