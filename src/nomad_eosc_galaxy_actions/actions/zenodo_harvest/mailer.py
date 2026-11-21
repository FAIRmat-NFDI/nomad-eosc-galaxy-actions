# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Send an email over SMTP (https://docs.python.org/3/library/smtplib.html).

The SMTP server is NOMAD's own: the `mail` section of `nomad.yaml`
(https://docs.nomad-lab.eu/reference/config.html#mail), set by the
administrators for the whole installation. Port 465 uses TLS from the start,
other ports switch to TLS (STARTTLS) when the server offers it. The login is
only sent over TLS.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, SecretStr

IMPLICIT_TLS_PORT = 465


class MailConfigError(ValueError):
    """The SMTP settings are incomplete or unsafe."""


class MailConfig(Protocol):
    """The fields of NOMAD's `mail` config that are used here."""

    enabled: bool
    with_login: bool
    host: str
    port: int
    user: str
    password: SecretStr
    from_address: str


class SmtpSettings(BaseModel):
    host: str
    port: int = 587
    sender: str
    user: str | None = None
    password: SecretStr | None = None  # never shown in logs or errors

    @classmethod
    def from_mail_config(cls, mail: MailConfig) -> SmtpSettings | None:
        """Settings from NOMAD's `mail` config, or None when mail is disabled.

        Raises:
            MailConfigError: mail is enabled without a host or sender, or the
                login lacks a user or password.
        """
        if not mail.enabled:
            return None
        if not mail.host or not mail.from_address:
            raise MailConfigError("mail.enabled needs mail.host and mail.from_address")
        password = mail.password.get_secret_value() if mail.password else ""
        if mail.with_login and not (mail.user and password):
            raise MailConfigError("mail.with_login needs mail.user and mail.password")
        return cls(
            host=mail.host,
            port=mail.port,
            sender=mail.from_address,
            user=mail.user if mail.with_login else None,
            password=password if mail.with_login else None,
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
