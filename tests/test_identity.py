"""Messages are drafted in the user's name, and channels that are not connected are reported, not faked.

Regression for: "Say hi to me on WhatsApp" / "email me 5 coffee jokes" failing again and again with
"the email contains placeholder text ('[Your Name]')" or "recipient ... not used before"."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from synapse import identity as ID
from synapse.graph import Plan, validate_plan
from synapse.memory import Memory
from synapse.runtime import Runtime
from tests.conftest import ScriptedLLM, approve, gen, plan, tool

TASK = {"mode": "task"}


# ---------------- units ----------------
def test_name_comes_from_what_the_user_said_or_stored_facts():
    facts = [{"text": "Siddhikesh Gavit"}, {"text": "Footballer"}, {"text": "Btech CSE"}]
    assert ID.resolve(None, None, facts, ["Replace it with actual data"])["name"] == "Siddhikesh Gavit"
    said = ["hi", "My name is Siddhikesh Gavit and I'm Footballer and in my last year"]
    assert ID.resolve(None, None, [], said)["name"] == "Siddhikesh Gavit"
    me = ID.resolve(None, None, [], ["my name is siddhu and my email is Siddhu@gmail.com"])
    assert me == {"name": "Siddhu", "email": "siddhu@gmail.com"}
    assert ID.resolve(None, None, [{"text": "Btech CSE"}, {"text": "Footballer"}], [])["name"] is None   # never guessed
    assert ID.resolve("Config Name", None, facts, [])["name"] == "Config Name"


def test_identity_slots_are_filled_and_data_slots_are_left_for_the_guard():
    body = "Hi [Recipient's Name],\n\n5 coffee jokes.\n\nBest regards,\n[Your Name]\n[Your Phone Number]"
    assert ID.fill(body, "Siddhikesh Gavit") == "Hi,\n\n5 coffee jokes.\n\nBest regards,\nSiddhikesh Gavit"
    assert ID.fill("Cheers, [Your Name]", None) == "Cheers,"
    assert "[Insert Temperature Here]" in ID.fill("Temp: [Insert Temperature Here]", "S")   # a missing fetch still fails


def test_channel_requests_are_recognised_without_catching_questions():
    assert ID.requested_channel("Say hi to me on WhatsApp number 9359279778.") == "whatsapp"
    assert ID.requested_channel("Send it to 9359279778 on WhatsApp.") == "whatsapp"
    assert ID.requested_channel("message the team on slack") == "slack"
    assert ID.requested_channel("What is WhatsApp's API pricing?") is None
    assert ID.requested_channel("Send me an email to a@b.com") is None
    assert ID.workflow_for("whatsapp", ["send_whatsapp", "slack"]) == "send_whatsapp"


def test_gmail_cannot_be_planned_for_a_phone_number():
    p = Plan(**plan([gen("s1"), tool("s2", "gmail_send", to=["9359279778"], subject="hi", body="{{s1}}")]))
    errs = validate_plan(p, {"gmail_send"}, 6, False, "say hi on whatsapp")
    assert any("not an email address" in e for e in errs)
    ok = Plan(**plan([gen("s1"), tool("s2", "gmail_send", to=["a@b.com"], subject="hi", body="{{s1}}")]))
    assert not validate_plan(ok, {"gmail_send"}, 6, False, "email a@b.com")


# ---------------- end to end: real graph, real MCP servers, real HTTP webhook ----------------
class Hook(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        Hook.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):
        pass


async def remember_name(settings, text="Siddhikesh Gavit"):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await Memory(settings.data_dir / "memory.db").add_fact(text, "profile", "earlier-run")


async def test_whatsapp_message_is_signed_with_the_users_name_and_sent(settings):
    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Hook.got = []
    settings.n8n_webhooks = f"whatsapp=http://127.0.0.1:{srv.server_port}/hook"
    await remember_name(settings)
    p = plan([gen("s1", "Write a short hi message"),
              {"id": "s2", "kind": "tool", "description": "send on WhatsApp", "tool": "run_workflow",
               "args": {"name": "whatsapp", "payload": {"to": "9359279778", "message": "{{s1}}"}}}])
    llm = ScriptedLLM([TASK, p, "Hi! Hope your day is going well.\n\nBest regards,\n[Your Name]", "Sent.",
                       {"facts": []}])
    ap = approve(True)
    try:
        async with Runtime(settings, llm) as rt:
            d = await rt.run("Say hi to me on WhatsApp number 9359279778.", ap)
    finally:
        srv.shutdown()
    assert d["status"] == "success", d["answer"]
    assert "Name: Siddhikesh Gavit" in llm.calls[2][-1]["content"]          # the writer knew whose voice it was
    shown = ap.seen[0]["args"]["payload"]["message"]                        # what the user approved...
    assert shown.endswith("Best regards,\nSiddhikesh Gavit") and "[" not in shown
    assert Hook.got == [{"to": "9359279778", "message": shown}]             # ...is exactly what was sent


async def test_whatsapp_without_a_workflow_says_so_instead_of_failing(settings):
    llm = ScriptedLLM([TASK, {"facts": []}])                                # triage + memory; no planner call
    async with Runtime(settings, llm) as rt:
        d = await rt.run("Say hi to me on WhatsApp number 9359279778.")
    assert d["status"] == "success" and not d["plan"]["steps"]
    assert "no WhatsApp workflow is connected" in d["answer"] and "9359279778" in d["answer"]
    assert "SYNAPSE_N8N_WEBHOOKS=whatsapp=" in d["answer"]


def test_the_email_that_kept_failing_now_passes_the_send_guard():
    from synapse.graph import email_ready, personalise
    body = "Here are 5 coffee jokes:\n\n1. It got mugged.\n\nBest regards,\n[Your Name]"
    assert email_ready(body)[1]                                             # before: refused
    out, why = email_ready(personalise(body, {"name": "Siddhikesh Gavit"}))
    assert why is None and out.endswith("Siddhikesh Gavit")
