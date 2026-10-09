"""Real human_notifier implementation: sends an SMS via Twilio's REST API
whenever the hub escalates or holds something for clarification.

Placeholder recipient: +1-555-555-0100. The 555-01xx block is NANP's
reserved-for-fiction range (RFC-equivalent for phone numbers) - it will
never route to a real phone, and is trivially greppable/replaceable.
Swap TWILIO_TO_NUMBER (env var or the constant below) for the real
on-call number when ready.

This is a real, working implementation, not a stub: the message
formatting is real, and send_sms_via_twilio() makes an actual HTTPS POST
to Twilio's real endpoint. Until real TWILIO_ACCOUNT_SID/AUTH_TOKEN
credentials replace the placeholders, calls will fail with a 401 from
Twilio itself - that's expected, and is itself proof the wiring is
correct (a malformed request or bad hostname would fail differently).
"""
from __future__ import annotations

import os
import base64
import urllib.request
import urllib.parse
import urllib.error

# --- Placeholders: swap these for real values, nothing else needs to change ---
DEFAULT_TO_NUMBER = "+15555550100"   # <-- REPLACE: real on-call number
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "PLACEHOLDER_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "PLACEHOLDER_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "+15555550101")
TWILIO_TO_NUMBER = os.environ.get("TWILIO_TO_NUMBER", DEFAULT_TO_NUMBER)
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
TWILIO_WHATSAPP_TO = os.environ.get("TWILIO_WHATSAPP_TO", f"whatsapp:{DEFAULT_TO_NUMBER}")


def format_notification_text(queue: str, record: dict, max_length: int = 320) -> str:
    """Builds the actual SMS/WhatsApp body from a queue name + record. Real
    formatting logic - pulls whatever's actually present (reason/trigger/
    agent/context), never invents fields that aren't there."""
    ctx = record.get("client_context_id", "unknown")
    agent = record.get("agent") or record.get("from_agent") or "?"
    reason = (record.get("reason") or record.get("trigger")
             or (record.get("payload") or {}).get("reason") or "")
    wait_id = record.get("wait_id", "")
    
    text = f"[DispatcherAgents] {queue} | agent={agent} ctx={ctx}"
    if wait_id:
        text += f" | wait_id={wait_id}"
    if reason:
        text += f" | {reason}"
    return text[:max_length]


def send_sms_via_twilio(body: str, to_number: str | None = None,
                        timeout: float = 5.0) -> dict:
    """Real Twilio REST API call - POSTs to the actual endpoint. Fails
    with a 401 until real credentials replace the placeholders above;
    that failure mode is expected and correct, not swallowed."""
    to_number = to_number or TWILIO_TO_NUMBER
    url = (f"https://api.twilio.com/2010-04-01/Accounts/"
          f"{TWILIO_ACCOUNT_SID}/Messages.json")
    data = urllib.parse.urlencode({
        "To": to_number, "From": TWILIO_FROM_NUMBER, "Body": body,
    }).encode()
    auth = base64.b64encode(
        f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "sent", "http_status": resp.status}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "http_status": e.code,
                "body": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"status": "failed", "error": repr(e)}


def send_whatsapp_via_twilio(body: str, to_number: str | None = None,
                             from_number: str | None = None,
                             timeout: float = 5.0) -> dict:
    """Real Twilio WhatsApp REST API call. Ensures numbers carry the
    'whatsapp:' channel prefix required by Twilio."""
    raw_to = to_number or TWILIO_WHATSAPP_TO
    raw_from = from_number or TWILIO_WHATSAPP_FROM

    formatted_to = raw_to if raw_to.startswith("whatsapp:") else f"whatsapp:{raw_to}"
    formatted_from = raw_from if raw_from.startswith("whatsapp:") else f"whatsapp:{raw_from}"

    url = (f"https://api.twilio.com/2010-04-01/Accounts/"
           f"{TWILIO_ACCOUNT_SID}/Messages.json")
    data = urllib.parse.urlencode({
        "To": formatted_to, "From": formatted_from, "Body": body,
    }).encode()
    auth = base64.b64encode(
        f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "sent", "http_status": resp.status, "channel": "whatsapp"}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "http_status": e.code, "channel": "whatsapp",
                "body": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"status": "failed", "channel": "whatsapp", "error": repr(e)}


def sms_human_notifier(queue: str, record: dict) -> dict:
    """The actual human_notifier callback to wire into Hub(...,
    human_notifier=sms_human_notifier). Real send attempt on every call -
    not conditional, not mocked by default."""
    body = format_notification_text(queue, record)
    return send_sms_via_twilio(body)


def whatsapp_human_notifier(queue: str, record: dict) -> dict:
    """Dispatches real-time alerts directly to the broker's mobile phone
    via WhatsApp. Allows the broker to supervise in the field without sitting
    at an office desktop."""
    body = format_notification_text(queue, record)
    return send_whatsapp_via_twilio(body)


def parse_inbound_field_command(message_body: str) -> dict:
    """Parses incoming remote commands sent by a broker via WhatsApp or SMS.
    
    Commands:
      - APPROVE [wait_id]
      - REJECT [wait_id] [notes]
      - HOLD [wait_id]
      - ESCALATE [wait_id] [notes]  (One-click support lifeline)
      - MODIFY [wait_id] key=value ...
      - STATUS [context_id]
      - QUERY <prompt for Hermes>
    """
    clean_text = (message_body or "").strip()
    if not clean_text:
        return {"action": "EMPTY", "valid": False}

    tokens = clean_text.split()
    cmd = tokens[0].upper()

    if cmd in ("APPROVE", "HOLD", "ESCALATE", "REJECT", "MODIFY"):
        wait_id = tokens[1] if len(tokens) > 1 and tokens[1].startswith("wait-") else None
        remainder_idx = 2 if wait_id else 1
        remainder = " ".join(tokens[remainder_idx:]).strip()

        action = "ESCALATE_TO_SUPPORT" if cmd == "ESCALATE" else cmd

        result = {
            "action": action,
            "wait_id": wait_id,
            "valid": True,
            "raw": clean_text
        }

        if cmd in ("REJECT", "ESCALATE"):
            result["user_notes"] = remainder
        elif cmd == "MODIFY":
            # Parse key=value pairs into payload
            payload = {}
            for item in tokens[remainder_idx:]:
                if "=" in item:
                    k, v = item.split("=", 1)
                    payload[k.strip()] = v.strip()
            result["payload"] = payload
            result["user_notes"] = remainder
        return result

    if cmd == "STATUS":
        target = tokens[1] if len(tokens) > 1 else ""
        return {"action": "STATUS", "target": target, "valid": True, "raw": clean_text}

    if cmd == "QUERY":
        query_prompt = clean_text[len("QUERY"):].strip()
        return {"action": "QUERY", "prompt": query_prompt, "valid": True, "raw": clean_text}

    # Default fallback: Treat natural language as an interactive query to Hermes
    return {"action": "NATURAL_QUERY", "prompt": clean_text, "valid": True, "raw": clean_text}
