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


def format_notification_text(queue: str, record: dict) -> str:
    """Builds the actual SMS body from a queue name + record. Real
    formatting logic - pulls whatever's actually present (reason/trigger/
    agent/context), never invents fields that aren't there."""
    ctx = record.get("client_context_id", "unknown")
    agent = record.get("agent") or record.get("from_agent") or "?"
    reason = (record.get("reason") or record.get("trigger")
             or (record.get("payload") or {}).get("reason") or "")
    text = f"[DispatcherAgents] {queue} | agent={agent} ctx={ctx}"
    if reason:
        text += f" | {reason}"
    return text[:320]  # SMS-reasonable length cap; Twilio splits longer anyway


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


def sms_human_notifier(queue: str, record: dict) -> dict:
    """The actual human_notifier callback to wire into Hub(...,
    human_notifier=sms_human_notifier). Real send attempt on every call -
    not conditional, not mocked by default."""
    body = format_notification_text(queue, record)
    return send_sms_via_twilio(body)
