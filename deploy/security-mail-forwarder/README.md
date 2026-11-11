# security@a2a-settlement.org forwarder

Mail for `a2a-settlement.org` is received by Resend (MX `receive.forge.rmta.net`).
Resend posts an `email.received` webhook to `https://a2a-settlement.org/hooks/resend-inbound`;
Caddy proxies that path to this service on `127.0.0.1:8011` on the SettleBridge droplet.
The service verifies the Svix signature and re-sends messages addressed to
`security@a2a-settlement.org` to the private inbox in `FORWARD_TO`, with the original
attached as `original.eml`.

Install (droplet, as root):

```
install -D -m 0644 forwarder.py /opt/a2a-security-mail/forwarder.py
install -m 0644 a2a-security-mail.service /etc/systemd/system/
# /etc/a2a-security-mail.env, mode 600: RESEND_API_KEY, RESEND_WEBHOOK_SECRET,
# FORWARD_TO, FORWARD_FROM, ACCEPT_ADDRESSES, LISTEN_PORT=8011
systemctl daemon-reload && systemctl enable --now a2a-security-mail
```

Caddy, inside the `a2a-settlement.org` block:

```
handle /hooks/resend-inbound {
	reverse_proxy 127.0.0.1:8011
}
```

Tests: `cd deploy/security-mail-forwarder && python -m pytest -q`.
