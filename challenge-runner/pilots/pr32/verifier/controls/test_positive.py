"""Positive control: an authorized reader can see and lease the private bounty."""


def test_reader_sees_and_can_lease_the_private_bounty(sbc_world):
    reader = sbc_world.client_as("reader")
    private_id = str(sbc_world.ids["private"])
    detail = reader.get(f"/api/bounties/{private_id}")
    assert detail.status_code == 200, detail.text
    listed = reader.get("/api/bounties")
    assert listed.status_code == 200
    assert private_id in {item["id"] for item in listed.json()["bounties"]}
    status = reader.get(f"/api/v1/github/bounties/{private_id}/status")
    assert status.status_code == 200, status.text
    shared = reader.get(f"/api/submissions/{sbc_world.submission_ids['private']}")
    assert shared.status_code == 200, shared.text
