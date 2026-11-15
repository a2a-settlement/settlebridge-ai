// Runs with `npm run test:unit` (node --test, built-in type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  agentCounts,
  extractApiError,
  formatActivityDate,
  hasGatewayTabAccess,
  listedManagedAgents,
  truncateAgentId,
  visibleManagedAgents,
} from "../lib/managedAgents.ts";

test("hasGatewayTabAccess follows exact Gateway tab visibility rules", () => {
  // Hidden when signed out even if gateway fields exist
  assert.equal(
    hasGatewayTabAccess({
      signedIn: false,
      callerAccountType: "gateway",
      bots: [],
      gatewayClaimedAgents: [],
    }),
    false
  );

  // Hidden when signed in as standard agent operator with no gateway claims
  assert.equal(
    hasGatewayTabAccess({
      signedIn: true,
      callerAccountType: "agent",
      bots: [
        {
          id: "a1",
          bot_name: "Alpha",
          status: "active",
          developer_id: "dev-1",
          gateway_claims: [],
        },
      ],
      gatewayClaimedAgents: [],
    }),
    false
  );

  // Shown when at least one owned agent has an active gateway_claims entry
  assert.equal(
    hasGatewayTabAccess({
      signedIn: true,
      callerAccountType: "agent",
      bots: [
        {
          id: "a1",
          bot_name: "Alpha",
          status: "active",
          developer_id: "dev-1",
          gateway_claims: [
            {
              gateway_id: "gw-1",
              gateway_name: "Trust Gateway",
              verified: true,
              claimed_at: "2026-11-15T00:00:00Z",
            },
          ],
        },
      ],
    }),
    true
  );

  // Shown when caller_account_type is gateway even with zero claimed agents
  assert.equal(
    hasGatewayTabAccess({
      signedIn: true,
      callerAccountType: "gateway",
      bots: [],
      gatewayClaimedAgents: [],
    }),
    true
  );

  // Shown when gateway_claimed_agents is non-empty
  assert.equal(
    hasGatewayTabAccess({
      signedIn: true,
      callerAccountType: "agent",
      bots: [],
      gatewayClaimedAgents: [
        {
          id: "a2",
          bot_name: "Claimed Agent",
          status: "active",
          developer_id: "dev-2",
        },
      ],
    }),
    true
  );
});

test("listedManagedAgents excludes the operator linked account from counts", () => {
  const bots = [
    {
      id: "own-flag",
      bot_name: "Mine",
      status: "active",
      developer_id: "d",
      own: true,
      has_agent_card: true,
    },
    {
      id: "linked-id",
      bot_name: "Linked",
      status: "active",
      developer_id: "d",
      own: false,
      has_agent_card: false,
    },
    {
      id: "sib",
      bot_name: "Sib",
      status: "suspended",
      developer_id: "d",
      has_agent_card: false,
    },
    {
      id: "peer",
      bot_name: "Peer",
      status: "active",
      developer_id: "d",
      has_agent_card: true,
    },
  ];
  const listed = listedManagedAgents(bots, "linked-id");
  assert.deepEqual(
    listed.map((b) => b.id),
    ["sib", "peer"]
  );
  assert.deepEqual(agentCounts(listed), {
    active: 1,
    suspended: 1,
    missingCard: 1,
  });
  assert.deepEqual(agentCounts(bots).active, 3);
});

test("dashboard and My agents tab share the non-test suspended count", () => {
  const ownId = "linked-account";
  const bots = [
    {
      id: "own-flag",
      bot_name: "Own flag",
      status: "suspended",
      developer_id: "d",
      own: true,
      is_test: false,
    },
    {
      id: ownId,
      bot_name: "Linked account",
      status: "suspended",
      developer_id: "d",
      is_test: false,
    },
    {
      id: "test-suspended",
      bot_name: "Test suspended",
      status: "suspended",
      developer_id: "d",
      is_test: true,
    },
    {
      id: "test-active",
      bot_name: "Test active",
      status: "active",
      developer_id: "d",
      is_test: true,
    },
    {
      id: "real-suspended",
      bot_name: "Real suspended",
      status: "suspended",
      developer_id: "d",
      is_test: false,
    },
    {
      id: "real-suspended-unset",
      bot_name: "Real suspended unset",
      status: "suspended",
      developer_id: "d",
    },
    {
      id: "real-active",
      bot_name: "Real active",
      status: "active",
      developer_id: "d",
      has_agent_card: false,
    },
  ];

  // Default My agents tab: drop the linked account and hide is_test.
  const tabWouldShow = bots.filter(
    (b) =>
      !b.own &&
      b.id !== ownId &&
      b.is_test !== true &&
      b.status === "suspended"
  ).length;
  const visible = visibleManagedAgents(bots, ownId);
  const dashboardSuspended = agentCounts(visible).suspended;
  const tabSuspended = visible.filter((b) => b.status === "suspended").length;

  assert.equal(tabWouldShow, 2);
  assert.equal(dashboardSuspended, tabWouldShow);
  assert.equal(tabSuspended, tabWouldShow);
  assert.equal(agentCounts(visible).active, 1);
});

test("agentCounts tracks active, suspended, and missingCard", () => {
  const counts = agentCounts([
    {
      id: "1",
      bot_name: "A",
      status: "active",
      developer_id: "d",
      has_agent_card: true,
    },
    {
      id: "2",
      bot_name: "B",
      status: "active",
      developer_id: "d",
      has_agent_card: false,
    },
    {
      id: "3",
      bot_name: "C",
      status: "suspended",
      developer_id: "d",
      has_agent_card: false,
    },
  ]);
  assert.deepEqual(counts, { active: 2, suspended: 1, missingCard: 2 });
});

test("extractApiError formats string and FastAPI validation array details", () => {
  assert.equal(
    extractApiError(
      { response: { data: { detail: "Developer ID already claimed" } } },
      "Fallback"
    ),
    "Developer ID already claimed"
  );
  assert.equal(
    extractApiError(
      {
        response: {
          data: {
            detail: [
              { loc: ["body", "settlement", "exchange_url"], msg: "Field required" },
            ],
          },
        },
      },
      "Fallback"
    ),
    "settlement.exchange_url: Field required"
  );
  assert.equal(extractApiError(new Error("network"), "Fallback"), "Fallback");
});

test("formatActivityDate and truncateAgentId handle edge cases", () => {
  assert.equal(formatActivityDate(null), "—");
  assert.equal(formatActivityDate("not-a-date"), "—");
  assert.notEqual(formatActivityDate("2026-11-15T12:00:00Z"), "—");
  assert.equal(truncateAgentId("short-id"), "short-id");
  assert.equal(
    truncateAgentId("12345678-aaaa-bbbb-cccc-1234567890ab"),
    "12345678…7890ab"
  );
});
