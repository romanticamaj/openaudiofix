# Security

OpenAudioFix runs on your own computer and has no user accounts. It listens on `127.0.0.1` by default, answers only
the host names it knows and refuses changes sent from other websites.

**Do not expose it to a network you do not trust.** If you put it behind a reverse proxy, add authentication there.

## Reporting a vulnerability

Use [private vulnerability reporting](https://github.com/romanticamaj/openaudiofix/security/advisories/new). Please do not open a public issue. Include the version, steps to
reproduce and what an attacker gains.

This is a volunteer project, so there is no guaranteed response time. Fixes go into the latest release.
