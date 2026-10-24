# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Send an email over SMTP (https://docs.python.org/3/library/smtplib.html).

The SMTP settings come only from the worker's environment, so the password
never appears in a config file, a run folder or the Action's inputs:

- `HARVEST_SMTP_HOST`: SMTP server; without it no email is sent.
- `HARVEST_SMTP_PORT`: default 587. Port 465 uses TLS from the start,
  other ports switch to TLS (STARTTLS) when the server offers it.
- `HARVEST_SMTP_FROM`: sender address (required with a host).
- `HARVEST_SMTP_USER`, `HARVEST_SMTP_PASSWORD`: login, only over TLS.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Mapping, Sequence
from email.message import EmailMessage
from pathlib import Path

from pydantic import BaseModel, SecretStr

IMPLICIT_TLS_PORT = 465


class MailConfigError(ValueError):
    """The SMTP settings are incomplete or unsafe."""


class SmtpSettings(BaseModel):
    host: str
    port: int = 587
    sender: str
    user: str | None = None
    password: SecretStr | None = None  # never shown in logs or errors

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> SmtpSettings | None:
        """Settings from `HARVEST_SMTP_*` variables, or None without a host.

        Raises:
            MailConfigError: a host without sender, or only half a login.
        """
        host = environ.get("HARVEST_SMTP_HOST")
        if not host:
            return None
        sender = environ.get("HARVEST_SMTP_FROM")
        user = environ.get("HARVEST_SMTP_USER") or None
        password = environ.get("HARVEST_SMTP_PASSWORD") or None
        if not sender:
            raise MailConfigError("HARVEST_SMTP_FROM is needed with HARVEST_SMTP_HOST")
        if bool(user) != bool(password):
            raise MailConfigError(
                "set both HARVEST_SMTP_USER and HARVEST_SMTP_PASSWORD, or neither"
            )
        return cls(
            host=host,
            port=int(environ.get("HARVEST_SMTP_PORT") or 587),
            sender=sender,
            user=user,
            password=password,
        )


def build_message(
    sender: str, to: str, subject: str, body: str, attachments: Sequence[Path] = ()
) -> EmailMessage:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message.set_content(body)
    for path in attachments:
        message.add_attachment(
            Path(path).read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=Path(path).name,
        )
    return message


def send_mail(
    settings: SmtpSettings, message: EmailMessage, timeout: float = 60
) -> None:
    """Send one message.

    Raises:
        MailConfigError: a login is configured, but the server offers no TLS.
        smtplib.SMTPException, OSError: the server refused or is unreachable.
    """
    context = ssl.create_default_context()
    if settings.port == IMPLICIT_TLS_PORT:
        server = smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=timeout, context=context
        )
    else:
        server = smtplib.SMTP(settings.host, settings.port, timeout=timeout)
    with server:
        encrypted = settings.port == IMPLICIT_TLS_PORT
        server.ehlo()
        if not encrypted and server.has_extn("starttls"):
            server.starttls(context=context)
            server.ehlo()
            encrypted = True
        if settings.user:
            if not encrypted:
                raise MailConfigError(
                    f"{settings.host} offers no TLS; refusing to send the password"
                )
            server.login(settings.user, settings.password.get_secret_value())
        server.send_message(message)
