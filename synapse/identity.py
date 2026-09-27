"""Who the user is, and filling a draft with it.

Why this exists: the writer that drafts an email or message was only ever given the goal, so it did
not know whose voice it was writing in and signed "[Your Name]". The send guard then (correctly)
refused the draft, and the replan could not fix it either, because nothing in the run held the name.

Three pieces:
- resolve(): the user's name and own email, from config, from what they told us ("my name is ..."),
  or from stored profile facts.
- fill(): replace identity placeholders in outgoing text deterministically ("[Your Name]" -> the name,
  "Hi [Recipient's Name]," -> "Hi,"). Placeholders for DATA ("[Insert Temperature]") are left alone so
  the send guard still refuses them: those mean a fetch step is missing.
- requested_channel(): "send it on WhatsApp" names a channel. If no workflow for it is registered,
  the honest answer is to say so, not to push the message through gmail_send.
"""
from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# "my name is Siddhikesh Gavit and I'm ..." -> "Siddhikesh Gavit"
_NAME_WORD = r"[A-Za-z][A-Za-z'.-]*"
NAME_SAID = re.compile(rf"\b(?:my name is|my name's|i am called|i'm called|call me|name\s*[:=])\s+({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})",
                       re.I)
NOT_NAME = {"and", "i", "im", "i'm", "from", "a", "an", "the", "currently", "working", "studying", "at", "in", "but",
            "so", "who", "here", "doing", "fine", "good", "not", "just", "also", "your", "my", "is", "it", "this", "that"}
# a stored fact that IS a name: "Name: Siddhikesh Gavit", "User's name is X", or just "Siddhikesh Gavit"
NAME_FACT = re.compile(rf"^\s*(?:(?:the\s+)?(?:user'?s?\s+|my\s+|full\s+)?name(?:\s+is)?\s*[:=-]?\s*)({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})\s*\.?\s*$",
                       re.I)
BARE_NAME = re.compile(r"^\s*([A-Z][a-z'.-]+(?:\s+[A-Z][a-z'.-]+){1,3})\s*\.?\s*$")   # 2-4 Capitalised words, no ALL-CAPS
EMAIL_SAID = re.compile(r"\b(?:my|own)\s+(?:e-?mail|mail|gmail|mail\s*id|email\s*id|id)(?:\s+(?:address|id))?\s*(?:is|:|=)?\s*"
                        r"([\w.+-]+@[\w-]+\.[\w.-]+)", re.I)


def _clean_name(raw: str) -> str | None:
    words = []
    for w in raw.split():
        if w.lower().strip(".'") in NOT_NAME:
            break
        words.append(w.strip(".,"))
    if not words:
        return None
    name = " ".join(words[:4])
    return name.title() if name.islower() else name


def name_from_text(text: str) -> str | None:
    m = NAME_SAID.search(text or "")
    return _clean_name(m.group(1)) if m else None


def name_from_fact(text: str) -> str | None:
    t = (text or "").strip()
    explicit = re.match(r"^\s*(?:(?:the\s+)?(?:user'?s?\s+|my\s+|full\s+)?name)", t, re.I)
    if explicit:
        m = NAME_FACT.match(t)
        return _clean_name(m.group(1)) if m else None
    m = BARE_NAME.match(t)
    return m.group(1) if m else None


def resolve(configured_name: str | None, configured_email: str | None, facts: list[dict], said: list[str]) -> dict:
    """The user's name and own email. Order: config, then the user's own words ("my name is ..."),
    newest first, then stored profile facts. Never guessed from anything the model wrote."""
    name = (configured_name or "").strip() or None
    email = (configured_email or "").strip().lower() or None
    for text in said:                                     # newest first
        name = name or name_from_text(text)
        if not email:
            m = EMAIL_SAID.search(text or "")
            email = m.group(1).lower() if m else None
    for f in facts:
        name = name or name_from_fact(f.get("text", ""))
        if not email and re.match(r"^\s*(?:my\s+|own\s+|user'?s?\s+)?e-?mail", f.get("text", ""), re.I):
            m = EMAIL.search(f.get("text", ""))
            email = m.group(0).lower() if m else None
    return {"name": name, "email": email}


# ---------------- filling a draft ----------------
_OPEN, _CLOSE = r"[\[\{<]\s*", r"\s*[\]\}>]"
RECIPIENT_PH = re.compile(_OPEN + r"(?:recipient|receiver|friend|contact|their|his|her|addressee|customer|client)'?s?\s+"
                          r"(?:full\s+|first\s+)?name" + _CLOSE, re.I)
GREETING_NAME_PH = re.compile(r"(\b(?:hi|hello|hey|dear|greetings)\b)[ \t]*" + _OPEN + r"(?:first\s+|full\s+)?name" + _CLOSE, re.I)
SELF_NAME_PH = re.compile(_OPEN + r"(?:(?:your|my|sender|user|author)'?s?\s+)?(?:full\s+|first\s+)?name" + _CLOSE, re.I)
SELF_EMAIL_PH = re.compile(_OPEN + r"(?:your|my|sender)'?s?\s+e-?mail(?:\s+address)?" + _CLOSE, re.I)
# anything else about the sender: "[Your Phone Number]", "[Your Position]", "[Your Contact Information]"
SELF_OTHER_PH = re.compile(_OPEN + r"(?:your|my|sender)'?s?\s+[^\]\}>\n]{1,40}" + _CLOSE, re.I)


def fill(text: str, name: str | None, email: str | None = None) -> str:
    """Put the user's identity into a draft and drop sender/recipient template slots it cannot fill.

    Only identity slots are touched. A data slot such as "[Insert Temperature Here]" is left in place
    so the send guard still refuses the draft."""
    if not text or not isinstance(text, str):
        return text
    t = RECIPIENT_PH.sub("", text)
    t = GREETING_NAME_PH.sub(r"\1", t)
    t = SELF_NAME_PH.sub(name or "", t)
    t = SELF_EMAIL_PH.sub(email or "\x00", t)
    t = SELF_OTHER_PH.sub("\x00", t)                     # mark lines that only existed to hold a template slot
    lines = []
    for line in t.split("\n"):
        if "\x00" in line:
            continue
        line = re.sub(r"[ \t]+([,.!?;:])", r"\1", line)  # "Hi ," -> "Hi,"
        line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


# ---------------- channels ----------------
CHANNELS = {"whatsapp": "WhatsApp", "sms": "SMS", "telegram": "Telegram", "slack": "Slack"}
_CH = r"(whats\s?app|sms|telegram|slack)"
CHANNEL_ASK = re.compile(rf"\b(?:on|via|through|over|using|by|in|to (?:my|his|her|their)?)\s*{_CH}\b"
                         rf"|\b{_CH}\s+(?:message|msg|him|her|them|me|to|number|no\b|chat|dm)"
                         rf"|^\s*{_CH}\s+\w", re.I)
SEND_WORD = re.compile(r"\b(send|sent|message|msg|text|ping|say|tell|dm|notify|forward|share|reply|remind|wish|drop|post|"
                       r"whats\s?app|sms)\b", re.I)


def requested_channel(text: str) -> str | None:
    """'Say hi to me on WhatsApp' -> 'whatsapp'. 'What's WhatsApp's pricing?' -> None."""
    m = CHANNEL_ASK.search(text or "")
    if not m or not SEND_WORD.search(text):
        return None
    word = next(g for g in m.groups() if g).lower().replace(" ", "")
    return word


def workflow_for(channel: str, workflows: list[str]) -> str | None:
    for wf in workflows or []:
        if channel in wf.lower().replace(" ", "").replace("_", "").replace("-", ""):
            return wf
    return None


def not_connected_answer(channel: str, goal: str) -> str:
    label = CHANNELS.get(channel, channel)
    number = re.search(r"\+?\d[\d\s-]{7,}\d", goal or "")
    to = f" to {number.group(0).strip()}" if number else ""
    return (f"I can't send {label} messages yet: no {label} workflow is connected, so nothing was sent{to}.\n\n"
            f"To turn it on, build an n8n workflow (Webhook trigger → {label} node) and register it in `.env`:\n\n"
            f"```\nSYNAPSE_N8N_WEBHOOKS={channel}=https://<your-n8n>/webhook/<id>\n```\n\n"
            f"Restart Synapse and ask again. It will draft the message in your name and ask you to approve it "
            f"before sending. Email works now: give me an address.")
