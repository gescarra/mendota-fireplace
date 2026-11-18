# Proflame 2 fireplace for Home Assistant, through a Broadlink RM4 Pro

Controls a SIT Proflame 2 gas fireplace by sending the handheld remote's
315 MHz packets from a Broadlink RM4 Pro that Home Assistant already knows.
No extra hardware, no soldering.

Developed against a Mendota fireplace with the GTMFSLA transmitter
(FCC ID T99058404300), which uses the extended 10-word packet. The older
7-word packet is implemented from public documentation but has not been tried
on a real fireplace.

If you would rather use a dedicated CC1101 radio, which can also listen to the
handheld remote, see [HACS-Proflame2](https://github.com/jeffgregx2/HACS-Proflame2).

## Safety

This lights a gas appliance from software. The fireplace never reports back,
so Home Assistant shows what it last sent, not what the fireplace is doing.
Do not automate it in ways you would not trust unattended.

## What you get

- Switches: power, continuous pilot, secondary burner
- Flame slider, 0 to 6
- Fan, 6 speeds
- Accent light, 6 levels
- Fireplace `climate` entity that gathers on/off, flame, secondary burner
  and the optional thermostat in one place, for HomeKit (see below)

## Install

1. Add this repository to HACS as a custom integration repository, install
   **Proflame 2 Fireplace (Broadlink)** and restart Home Assistant. Or copy
   `custom_components/proflame2_broadlink` into `config/custom_components/`.
2. Learn one code from your handheld remote. Any button will do, since every
   packet carries the remote's identity. Either:
   - Developer tools → Actions → `remote.learn_command` on the RM4 Pro with
     `command_type: rf`, then copy the code from
     `config/.storage/broadlink_remote_*_codes`; or
   - run `tools/proflame.py listen --host <rm4-ip> --save captures` and use
     any file it writes; or
   - record a RAW capture at 314.97 MHz with a Flipper Zero.
3. Settings → Devices & services → Add integration →
   **Proflame 2 Fireplace (Broadlink)**. Pick the RM4 Pro's `remote` entity
   and paste the code.

**Treat that code like a key.** It contains your remote's serial, and anyone
within radio range who has it can operate your fireplace. Do not post it in
issues or commit it.

## The handheld remote will fight you

The remote re-sends its own idea of the state every few minutes, and the
fireplace obeys whichever packet came last. Take the batteries out of the
remote, or expect it to undo what Home Assistant did. This integration also
re-sends its state every 5 minutes (configurable, 0 disables).

## Thermostat

The fireplace has no thermostat of its own: the handheld remote measures the
room and tells the fireplace what flame to burn. To get the same from Home
Assistant, choose a room temperature sensor during setup or in the
integration's options. The fireplace `climate` entity then gets a target
temperature and three presets:

- **Manual**: burn the chosen flame level.
- **Thermostat**: full flame until the room is past the target by the margin,
  then off until it is under by the margin.
- **Smart**: the flame follows how far the room is below target. This is our
  own curve, not the handheld remote's. Three options shape it: the gap at
  which the flame reaches 6 (default 6 °F, so one level per degree), the
  lowest flame level used, and the margin (default 0.5 °F).
- Choosing a flame level on the fireplace entity switches to Manual. Setting
  a temperature switches back to the last thermostat preset used. That is how
  HomeKit, which cannot pick presets, moves between them.
- If the sensor becomes unavailable while heating, the flame is turned off.
- Turning on the manual power switch leaves thermostat mode.

## HomeKit

HomeKit Bridge gives every entity its own tile, so the controls that belong
together are on one entity, `climate.fireplace`, shaped to fit HomeKit's
Heater Cooler:

| In the Home app | On the entity |
|---|---|
| On/off | heat / off |
| Fan speed slider | flame, in 4 steps: 1, 3, 4, 6 (fan modes low, middle, medium, high) |
| Swing | secondary burner |
| Target and room temperature | only with a room sensor |

The flame number entity keeps all six levels for Home Assistant. Expose the
blower and the accent light alongside it; they stay separate tiles, so put
them in the same room. HomeKit Bridge cannot show the flame number entity.

```yaml
homekit:
  - name: Home Assistant
    filter:
      include_entities:
        - climate.fireplace
        - fan.fireplace_fan
        - light.fireplace_accent_light
    entity_config:
      climate.fireplace:
        type: heater_cooler
```

Home Assistant 2026.11 picks Heater Cooler for this entity on its own the
first time it is exposed; `type` makes it explicit. An installation from
before this change keeps the entity ID `climate.fireplace_thermostat`; rename
it, or use that ID above.

## Dashboard

No custom card is needed. For example:

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: climate.fireplace
  - type: tile
    entity: switch.fireplace_power
  - type: tile
    entity: number.fireplace_flame
    features:
      - type: numeric-input
        style: slider
  - type: tile
    entity: fan.fireplace_fan
    features:
      - type: fan-speed
  - type: tile
    entity: light.fireplace_accent_light
    features:
      - type: light-brightness
```

## Protocol

Documented at the top of
[protocol.py](custom_components/proflame2_broadlink/protocol.py): framing,
the packet layout, and the checksum, which differs from the published one.
Thanks to [rtl_433](https://github.com/merbanan/rtl_433) and
[smartfire](https://github.com/johnellinwood/smartfire) for documenting the
7-word packet that this builds on.

## Development

    uv venv .venv && uv pip install --python .venv/bin/python -r requirements-test.txt broadlink
    .venv/bin/python -m pytest tests
    .venv/bin/python tools/proflame.py decode <file | base64 | hex>
    .venv/bin/python tools/proflame.py listen --host <rm4-ip>

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately, as
described in [SECURITY.md](SECURITY.md).

## License

[MIT-0](LICENSE). Do what you like with it, no attribution required.
