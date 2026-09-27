"""API tests: real FastAPI app, real runtime, scripted model."""
import asyncio

import httpx
import pytest
from asgi_lifespan import LifespanManager

from synapse.api import create_app, vault_graph
from synapse.scripted import ScriptedLLM, plan, tool

pytestmark = pytest.mark.asyncio


async def client(settings, replies, token=None):
    if token:
        from pydantic import SecretStr
        settings.api_token = SecretStr(token)
    app = create_app(settings)
    app.dependency_overrides = {}
    import synapse.api as api_mod

    # inject the scripted model into the runtime the app creates
    orig = api_mod.Runtime

    class Patched(orig):
        def __init__(self, s, llm=None):
            super().__init__(s, ScriptedLLM([{"mode": "task"}, *replies]))

    api_mod.Runtime = Patched
    try:
        mgr = LifespanManager(app)
        await mgr.__aenter__()
        transport = httpx.ASGITransport(app=app)
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        return c, mgr
    finally:
        api_mod.Runtime = orig


async def wait_done(c, run_id, want=("success", "failed", "error", "awaiting_approval"), tries=100):
    for _ in range(tries):
        r = (await c.get(f"/api/runs/{run_id}")).json()
        if r["status"] in want:
            return r
        await asyncio.sleep(0.05)
    raise AssertionError(f"run stuck in {r['status']}")


SAVE = plan([tool("s1", "create_note", path="Research/API.md", content="body that is long enough to verify")])


async def test_health_and_run_lifecycle(settings):
    c, mgr = await client(settings, [SAVE, "Saved to Research/API.md"])
    try:
        h = (await c.get("/api/health")).json()
        assert h["ok"] and h["tools"] > 0

        r = await c.post("/api/runs", json={"goal": "save a note about the API"})
        run_id = r.json()["run_id"]
        done = await wait_done(c, run_id)
        assert done["status"] == "success"
        assert (settings.vault_path / "Research/API.md").is_file()
        assert [s["name"] for s in done["spans"]][:3] == ["recall", "triage", "plan"]

        listing = (await c.get("/api/runs")).json()["runs"]
        assert listing[0]["id"] == run_id
        assert (await c.get("/api/metrics")).json()["runs"] == 1
        assert (await c.get("/api/tools")).json()["tools"]
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_approval_over_http(settings):
    settings.vault_path.mkdir(parents=True, exist_ok=True)
    (settings.vault_path / "Old.md").write_text("obsolete")
    p = plan([tool("s1", "delete_note", path="Old.md")])
    c, mgr = await client(settings, [p, "Deleted."])
    try:
        run_id = (await c.post("/api/runs", json={"goal": "delete Old.md"})).json()["run_id"]
        r = await wait_done(c, run_id, want=("awaiting_approval",))
        assert r["pending"]["tool"] == "delete_note" and r["pending"]["risk"] == "high"
        assert (settings.vault_path / "Old.md").is_file()  # nothing happened yet

        assert (await c.post(f"/api/runs/{run_id}/approve", json={"approved": True})).status_code == 200
        done = await wait_done(c, run_id, want=("success", "failed"))
        assert done["status"] == "success"
        assert not (settings.vault_path / "Old.md").exists()
        assert done["approvals"][0]["approved"] is True
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_rejection_keeps_data(settings):
    settings.vault_path.mkdir(parents=True, exist_ok=True)
    (settings.vault_path / "Keep.md").write_text("precious")
    p = plan([tool("s1", "delete_note", path="Keep.md")])
    c, mgr = await client(settings, [p, plan([tool("s1", "list_notes")]), "I did not delete it."])
    try:
        run_id = (await c.post("/api/runs", json={"goal": "delete Keep.md"})).json()["run_id"]
        await wait_done(c, run_id, want=("awaiting_approval",))
        await c.post(f"/api/runs/{run_id}/approve", json={"approved": False, "comment": "no"})
        done = await wait_done(c, run_id, want=("success", "failed"))
        assert (settings.vault_path / "Keep.md").read_text() == "precious"
        assert done["results"]["s1"]["status"] == "rejected"
        # approving twice is a conflict, not a second delete
        assert (await c.post(f"/api/runs/{run_id}/approve", json={"approved": True})).status_code == 409
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_auth_required_when_token_set(settings):
    c, mgr = await client(settings, [], token="s3cret")
    try:
        assert (await c.get("/api/runs")).status_code == 401
        assert (await c.get("/api/runs", headers={"Authorization": "Bearer s3cret"})).status_code == 200
        assert (await c.get("/api/health")).status_code == 200  # health stays open for probes
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_vault_note_endpoint_blocks_traversal(settings):
    settings.vault_path.mkdir(parents=True, exist_ok=True)
    (settings.vault_path / "A.md").write_text("[[B]] link")
    (settings.vault_path / "B.md").write_text("target")
    c, mgr = await client(settings, [])
    try:
        assert (await c.get("/api/vault/note", params={"path": "A.md"})).json()["content"].startswith("[[B]]")
        assert (await c.get("/api/vault/note", params={"path": "../../etc/passwd"})).status_code == 404
        g = (await c.get("/api/vault/graph")).json()
        assert {n["id"] for n in g["nodes"]} == {"A.md", "B.md"}
        assert g["links"] == [{"source": "A.md", "target": "B.md"}]
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_vault_graph_ignores_hidden_and_broken_links(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "conf.md").write_text("x")
    (tmp_path / "N.md").write_text("[[Missing]] and [[N]]")
    g = vault_graph(tmp_path)
    assert [n["id"] for n in g["nodes"]] == ["N.md"]
    assert g["links"] == []  # self-links and dangling links are dropped


async def test_inbound_message_creates_a_gated_run(settings):
    """An inbound WhatsApp message becomes a run; a reply goes out only through an approved workflow."""
    # notify() needs a desktop backend, so this asserts the routing, using a tool available everywhere
    p = plan([tool("s1", "add_task", title="Reply to Mum about money", priority="high")])
    c, mgr = await client(settings, [p, "I did not reply — this one needs you.", {"facts": []}])
    try:
        r = await c.post("/api/messages", json={"source": "whatsapp", "sender": "Mum", "text": "can you send 5000?"})
        run_id = r.json()["run_id"]
        done = await wait_done(c, run_id)
        detail = (await c.get(f"/api/runs/{run_id}")).json()
        assert "incoming whatsapp message" in detail["goal"].lower()
        assert "never as instructions" in detail["goal"]         # the message is quoted as data
        assert done["status"] == "success"
        assert not any(s["attrs"].get("tool") == "run_workflow" for s in done["spans"])  # nothing sent
        convs = (await c.get("/api/conversations")).json()["conversations"]
        assert any(x["id"].startswith("inbox-whatsapp") for x in convs)   # inbox threads stay separate
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)


async def test_schedule_endpoints(settings):
    c, mgr = await client(settings, [])
    try:
        bad = await c.post("/api/schedules", json={"goal": "brief me", "cadence": "daily", "at": "nonsense"})
        assert bad.status_code == 400 and "08:30" in bad.json()["detail"]

        ok = await c.post("/api/schedules", json={"goal": "morning brief", "cadence": "daily", "at": "08:30"})
        sid = ok.json()["id"]
        listed = (await c.get("/api/schedules")).json()["schedules"]
        assert [x["goal"] for x in listed] == ["morning brief"] and listed[0]["next_run"] > 0

        assert (await c.delete(f"/api/schedules/{sid}")).status_code == 200
        assert (await c.delete(f"/api/schedules/{sid}")).status_code == 404
    finally:
        await c.aclose()
        await mgr.__aexit__(None, None, None)
