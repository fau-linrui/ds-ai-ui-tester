"""Snapshot diffs must not invent identities for indistinguishable controls."""
from tools.snapshot import diff_snapshots


def node(key, **fields):
    return {"key": key, **fields}


def test_duplicate_controls_are_reported_as_ambiguous_instead_of_matched_by_order():
    before = {"nodes": [
        node("button:Save", baseKey="button:Save", ambiguous=True, value="a"),
        node("button:Save#2", baseKey="button:Save", ambiguous=True, value="b"),
    ]}
    after = {"nodes": [
        node("button:Save", baseKey="button:Save", ambiguous=True, value="b"),
        node("button:Save#2", baseKey="button:Save", ambiguous=True, value="a"),
    ]}
    diff = diff_snapshots(before, after)
    assert diff["ambiguous"] == ["button:Save"]
    assert diff["changed"] == []
    assert diff["added"] == []
    assert diff["removed"] == []


def test_unique_control_changes_are_still_reported():
    before = {"nodes": [node("tid:save", value="a", disabled=True)]}
    after = {"nodes": [node("tid:save", value="b", disabled=False)]}
    diff = diff_snapshots(before, after)
    assert diff["ambiguous"] == []
    assert diff["changed"][0]["fields"] == ["value", "disabled"]
