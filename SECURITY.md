# Security

## Reporting a vulnerability

Please report security problems privately through
[GitHub's private vulnerability reporting](https://github.com/gescarra/ha-proflame2-broadlink/security/advisories/new),
not in a public issue. I'll acknowledge it within a week.

Things worth reporting:

- The integration leaking the learned code or remote serial, for example into
  logs, diagnostics or entity attributes
- Anything that makes the fireplace light or stay lit when Home Assistant
  thinks it is off, or ignore an off command
- The thermostat failing to shut the flame off when it should, such as when
  the room sensor goes unavailable

## Known and out of scope

Proflame 2 has no authentication or encryption. The serial is sent in the
clear with every button press, so anyone with a 315 MHz receiver near your
house can capture it and replay or forge commands. That is how the original
remote works and nothing in this integration can fix it. It's also why the
README tells you to treat the learned code like a key.

## Supported versions

Only the latest release gets fixes.
