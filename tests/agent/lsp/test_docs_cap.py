"""Regression test for #126948: diagnostic-insert sites must respect MAX_TRACKED_FILES.

Two sites create brand-new _DocState entries outside open_file's fresh-open
branch (the only _evict_lru_docs call site): _handle_publish_diagnostics (push
path) and _pull_document_diagnostics' relatedDocuments walk (pull path). Both
now route through _track_doc, so feeding hundreds of distinct URIs with no
open_file calls must stay bounded.
"""
from __future__ import annotations

from agent.lsp.client import MAX_TRACKED_FILES, _DocState, LSPClient


def _client() -> LSPClient:
    return LSPClient(server_id="cap-test", workspace_root="C:/tmp", command=["node", "x"])


def test_publish_diagnostics_stays_capped():
    client = _client()
    for i in range(MAX_TRACKED_FILES * 4):
        client._handle_publish_diagnostics({
            "uri": f"file:///proj/push{i:04d}.py",
            "diagnostics": [{"message": "x"}],
        })
    assert len(client._docs) <= MAX_TRACKED_FILES


def test_track_doc_evicts_only_never_opened():
    client = _client()
    for i in range(MAX_TRACKED_FILES * 4):
        client._track_doc(f"/proj/pull{i:04d}.py")
    assert len(client._docs) <= MAX_TRACKED_FILES
    assert all(doc.version < 0 for doc in client._docs.values())


def test_opened_docs_survive_sync_trim():
    client = _client()
    client._docs["/proj/keep.py"] = _DocState(version=0, text="x = 1")
    for i in range(MAX_TRACKED_FILES * 4):
        client._track_doc(f"/proj/spill{i:04d}.py")
    assert "/proj/keep.py" in client._docs
    assert len(client._docs) <= MAX_TRACKED_FILES + 1
