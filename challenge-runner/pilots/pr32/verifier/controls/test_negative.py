"""Negative control: an outsider reads only public material, so the observer stays silent."""


def test_outsider_reads_public_material_only(sbc_world):
    outsider = sbc_world.client_as("outsider")
    public_id = str(sbc_world.ids["public"])
    assert outsider.get(f"/api/bounties/{public_id}").status_code == 200
    assert outsider.get(f"/api/v1/github/bounties/{public_id}/status").status_code == 200
    badge = outsider.get(f"/api/share/{sbc_world.share_tokens['public']}/badge.svg")
    assert badge.status_code == 200
    anonymous = sbc_world.client_as(None)
    assert anonymous.get(f"/api/bounties/{public_id}").status_code == 200
