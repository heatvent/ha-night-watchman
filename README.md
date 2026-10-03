# Night Watchman

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/heatvent/ha-night-watchman)](https://github.com/heatvent/ha-night-watchman/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-blue.svg)](https://www.home-assistant.io/)

**GitHub:** [github.com/heatvent/ha-night-watchman](https://github.com/heatvent/ha-night-watchman)

![Night Watchman](https://raw.githubusercontent.com/heatvent/ha-night-watchman/main/custom_components/night_watchman/icon.png)

The Night Watchman checks on your house to make sure it is secure and that lights are turned off after everyone has gone to bed — and, if you turn the option on, after everyone has left for the day.

You choose every device. A new light, sensor, or switch is ignored until you add it.

---

## How it works

### Quiet house

A round only proceeds when the house has been quiet. A light that is simply left on does not count. Only a recent change does: a light switched, motion, or a door opened or closed. If something changed during the quiet period, the round is skipped and tried again later.

### What a secure round does

- Turns off monitored lights that are still on (when that checkbox is selected), plus any extra lights or switches you picked, such as a coffee maker.
- Leaves **Lights to Keep On** alone (bedrooms, lamps).
- Locks each selected door only when that lock is unlocked and its contact is closed.
- Reports an open door in the notice and does not lock it. Nothing is closed.
- Never locks the garage overhead door (refused by name).

### Night rounds (clock)

By default, rounds run inside a night window. The defaults are 1:00 AM through 5:00 AM, every 60 minutes, so attempts land at 1, 2, 3, 4, and 5.

### Away when everyone leaves (optional)

Off by default, so other home and away automations are left alone.

When **Secure When Everyone Is Away** is on:

1. You pick the people who must all be away.
2. When the last of those people leaves, Night Watchman waits for the quiet period.
3. It runs **one** secure round with the same lights and doors as a night round.
4. If that round was skipped because the house was still active, it waits another quiet period and tries again.
5. After a successful round, if you set a [Presence Simulation](https://github.com/slashback100/presence_simulation) switch, that switch is turned **on**.
6. When someone comes home, that Presence Simulation switch is turned **off**.

There is no daytime clock for this path. People leaving is the trigger. While the house is already secured by this away path, night rounds are skipped so Presence Simulation can keep control of the lights.

### Notices

Every round sends a notice to the notify service(s) you configure:

| Notice | Meaning |
|---|---|
| Skipped | The house was still active. The recent devices are named. |
| All clear | The round ran and nothing needed locking, turning off, or reporting. |
| Actions | What changed: each lock, light or switch, and any door still open. |
| Presence simulation started | Sent after a successful away secure round when that switch was turned on. |

Use one notify service name, or several separated by commas, for example `phones_group` or `phones_group, tablet`. Leave off the `notify.` prefix.

### Pause and status

| Entity | What it shows |
|---|---|
| Enabled | On allows rounds. Off skips night and away securing without removing the integration. |
| Last round | When the last round was checked (night or away). The result text is in the attributes. |
| Last activity | When a monitored light, motion sensor, or door last changed. |
| Last active device | Which of those devices changed. |

These times are remembered across a Home Assistant restart. Lights Night Watchman itself turns off during a round do not count as activity.

---

## Install

### HACS

1. **HACS → Integrations → ⋮ → Custom repositories**
2. URL: `https://github.com/heatvent/ha-night-watchman` · Category: **Integration**
3. Download **Night Watchman**, then **restart** Home Assistant
4. **Settings → Devices & services → Add integration → Night Watchman**

HACS follows **GitHub Releases** (`v1.0.17`, …), not the tip of `main`. After a release, use **⋮ → Update information** if the update is slow to appear.

The HACS store list may show a placeholder icon for custom integrations. That is a HACS limitation. After install, the icon appears under **Settings → Devices & services**.

### Manual

Copy only `custom_components/night_watchman` into your Home Assistant `custom_components` folder. Do not copy the rest of this repo, and do not rename the folder.

---

## Setup

| Setting | What it does |
|---|---|
| First round / Last round | Night window when clock rounds are allowed. Default 1:00 AM through 5:00 AM. |
| Minutes between rounds | How often a night round is attempted inside that window. Default 60. |
| Quiet period | How long monitored devices must stay unchanged before a round proceeds. Default 45 minutes. Also used after the last person leaves for the away path. |
| Notify service | Where notices go. One name or a comma-separated list. Leave off `notify.`. |
| Monitored Lights | A recent on or off skips the round. Also turned off when **Turn Off All Monitored Lights** is selected. |
| Monitored Motion Sensors | A recent motion change skips the round. Indoor cameras belong here if you want them counted. |
| Monitored Doors | A recent open or close skips the round. This list does not lock or close anything. |
| Turn Off All Monitored Lights | Turns off the monitored lights when a secure round runs. |
| Lights to Keep On | Stays on even when the box above is selected. |
| Other Lights/Devices to Turn Off | Extra lights or switches (coffee maker, 3D printer). Do not add strips that should stay on. |
| Doors to Lock | Up to four lock and contact pairs. Lock only if unlocked and the contact is closed. |
| Secure When Everyone Is Away | Off by default. When on, people leaving triggers one secure round after the quiet period. |
| People Who Must Be Away | Used only when the box above is on. Every person here must be away. |
| Presence Simulation Switch | Optional. Turned on after a successful away secure round. Turned off when someone comes home. |

Do not add the garage overhead door. Night Watchman will refuse to lock a device whose name says overhead, and it never closes a cover.

---

## Support

- Repository: [github.com/heatvent/ha-night-watchman](https://github.com/heatvent/ha-night-watchman)
- Issues: [github.com/heatvent/ha-night-watchman/issues](https://github.com/heatvent/ha-night-watchman/issues)

## Credits

Developed with [Cursor](https://cursor.com).

---

## Updates

Each release tag is `v` plus the version in `custom_components/night_watchman/manifest.json`. Version `1.0.17` is tag `v1.0.17`. Bump both together. HACS offers the new release. Restart Home Assistant after installing it.
