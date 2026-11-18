# Contributing

Bug reports, fixes and reports from other fireplaces are all welcome. This is a
side project, so replies may take a few days.

## Before you post anything

Every Proflame 2 packet carries the remote's serial, and the serial is all
anyone in radio range needs to run your fireplace. Do not paste learned codes,
Broadlink base64, Flipper captures or full `decode` output in issues or PRs.
If you need to share a packet, replace the first three bytes with `12 34 56`,
the same placeholder the tests use.

## Reporting a bug

Open an issue with:

- Home Assistant and integration versions
- Fireplace make and model, and the transmitter model or FCC ID on the back
  of the handheld remote
- What you sent, what the fireplace did, and what Home Assistant showed
- Debug logs, with the serial removed:

  ```yaml
  logger:
    logs:
      custom_components.proflame2_broadlink: debug
  ```

## Other fireplaces and remotes

Only the extended 10-word packet from a GTMFSLA remote has been tested on a
real fireplace. If you get the 7-word packet working, or find a remote that
behaves differently, an issue saying so is useful even without a PR.

## Pull requests

1. Fork, branch, keep the change focused.
2. Set up and run the tests:

   ```bash
   uv venv .venv && uv pip install --python .venv/bin/python -r requirements-test.txt broadlink
   .venv/bin/python -m pytest tests
   ```

3. Add a test for anything that changes packet encoding or the thermostat
   logic. Those are the parts that light gas, so they don't get merged on
   "works on my fireplace" alone.
4. User-facing strings go in `strings.json` and `translations/en.json`.

By contributing you agree your work is released under the [MIT-0 license](LICENSE).
