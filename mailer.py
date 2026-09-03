"""SMTP delivery for the digest.

Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO from the
environment (see .env.example). Port 465 uses implicit TLS; anything else
uses STARTTLS.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage

log = logging.getLogger("jobwatch.mailer")

REQUIRED = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_TO")


def missing_env() -> "list[str]":
    return [name for name in REQUIRED if not os.environ.get(name)]


def send_digest(subject: str, body: str) -> None:
    """Send the markdown digest as a plain-text email. Raises on failure."""
    missing = missing_env()
    if missing:
        raise RuntimeError("missing environment variables: " + ", ".join(missing))

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    # Google displays an App Password as "abcd efgh ijkl mnop". The password
    # itself has no spaces, so pasting it as shown would otherwise fail auth
    # with a bare "Username and Password not accepted".
    password = re.sub(r"\s+", "", os.environ["SMTP_PASS"])
    recipients = [addr.strip() for addr in os.environ["EMAIL_TO"].split(",") if addr.strip()]
    sender = os.environ.get("EMAIL_FROM") or user

    message = EmailMessage()
    # Keep the subject pure ASCII so it survives every mail client intact.
    message["Subject"] = subject.encode("ascii", "ignore").decode("ascii")
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.send_message(message)

    log.info("digest emailed to %s", ", ".join(recipients))
