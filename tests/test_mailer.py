# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import email
import socket

import pytest
from nomad.config.models.config import Mail

from nomad_eosc_galaxy_actions.mailer import (
    MailConfigError,
    SmtpSettings,
    build_message,
    send_mail,
)

# NOMAD's own `mail` config model, as read from nomad.yaml.
MAIL = {"enabled": True, "host": "smtp.example.org", "from_address": "bot@example.org"}


# SmtpSettings.from_mail_config


def test_disabled_mail_means_no_email():
    assert SmtpSettings.from_mail_config(Mail()) is None
    assert SmtpSettings.from_mail_config(Mail(**MAIL | {"enabled": False})) is None


def test_settings_from_mail_config():
    login = {"port": 465, "with_login": True, "user": "bot", "password": "secret"}
    settings = SmtpSettings.from_mail_config(Mail(**MAIL | login))

    assert (settings.host, settings.port, settings.user, settings.sender) == (
        "smtp.example.org",
        465,
        "bot",
        "bot@example.org",
    )
    assert settings.password.get_secret_value() == "secret"
    assert "secret" not in repr(settings)  # kept out of logs and errors


def test_login_is_only_used_with_with_login():
    mail = Mail(**MAIL | {"user": "bot", "password": "secret"})
    settings = SmtpSettings.from_mail_config(mail)

    assert (settings.user, settings.password) == (None, None)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"host": ""}, "mail.host"),
        ({"from_address": ""}, "mail.from_address"),
        ({"with_login": True, "user": "bot"}, "mail.password"),
        ({"with_login": True, "password": "secret"}, "mail.user"),
    ],
)
def test_incomplete_settings_are_refused(change, message):
    with pytest.raises(MailConfigError, match=message):
        SmtpSettings.from_mail_config(Mail(**MAIL | change))


# send_mail (against a local fake SMTP server)


def _settings(port: int, **extra) -> SmtpSettings:
    return SmtpSettings(host="127.0.0.1", port=port, sender="bot@example.org", **extra)


def test_send_mail_with_attachment(fake_smtp, tmp_path):
    attachment = tmp_path / "report.json"
    attachment.write_text('{"run_id": "run-1"}')
    message = build_message(
        "bot@example.org", "user@example.org", "Report", "# Report\n", [attachment]
    )

    send_mail(_settings(fake_smtp.port), message)

    [(sender, recipients, raw)] = fake_smtp.messages
    assert (sender, recipients) == ("bot@example.org", ["user@example.org"])
    # SMTP sends CRLF line endings; compare with the original text.
    received = email.message_from_bytes(
        raw.replace(b"\r\n", b"\n"), policy=email.policy.default
    )
    assert received["Subject"] == "Report"
    assert received.get_body().get_content() == "# Report\n"
    [part] = list(received.iter_attachments())
    assert part.get_filename() == "report.json"
    assert part.get_content() == b'{"run_id": "run-1"}'


def test_password_is_never_sent_without_tls(fake_smtp):
    settings = _settings(fake_smtp.port, user="bot", password="secret")
    message = build_message("bot@example.org", "user@example.org", "Report", "body")

    with pytest.raises(MailConfigError, match="offers no TLS"):
        send_mail(settings, message)
    assert fake_smtp.messages == []


def test_unreachable_server_raises():
    with socket.socket() as sock:  # a port that nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    message = build_message("bot@example.org", "user@example.org", "Report", "body")

    with pytest.raises(OSError):
        send_mail(_settings(port), message, timeout=5)
