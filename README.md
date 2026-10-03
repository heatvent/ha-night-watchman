# Night Watchman

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/heatvent/ha-night-watchman)](https://github.com/heatvent/ha-night-watchman/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-blue.svg)](https://www.home-assistant.io/)

**GitHub:** https://github.com/heatvent/ha-night-watchman

![Night Watchman](https://raw.githubusercontent.com/heatvent/ha-night-watchman/main/custom_components/night_watchman/icon.png)

The Night Watchman checks on your house to make sure it is secure and that all lights are turned off after everyone has gone to bed — and, if you want, after everyone has left for the day.

It makes a round only when the house has been quiet. If someone is still up, it waits and tries again later. A light that is simply left on does not count as activity. Only a recent change does: a light switched, motion, or a door opened or closed. You choose every device. A new light, sensor, or switch is ignored until you add it.

When a full round runs:

- Monitored lights that are still on are turned off, plus any extra lights or switches you picked, such as a coffee maker.
- Each lock is locked only when it is unlocked and its door contact is closed.
- An open door is left alone and named in the phone message. Nothing is closed.
- The garage overhead door is never locked.

### Away during the day

Pick the people who must all be away. After the quiet period, Night Watchman runs one full secure round with the same lights and doors. If you also pick a [Presence Simulation](https://github.com/slashback100/presence_simulation) switch, that switch is turned on afterward.

While Presence Simulation is on, later rounds on the same interval only check doors and locks. Lights are left alone so the simulation can keep running. Light changes from Presence Simulation do not count as activity for those lock-only rounds. When someone comes home, Night Watchman turns the Presence Simulation switch off.

A phone notice goes out after every round:

- **Skipped** when the house is still active, naming the devices that changed recently.
- **All clear** when a quiet round found nothing to lock, turn off, or report.
- **Actions** when something changed, naming each lock, light, switch, or open door.

Turn the **Enabled** switch off when you want the night off: guests, you are still up, or you are away and do not want the house touched. The integration stays in place, and you can turn the switch back on from a dashboard or an automation. Disabling the integration itself stops it completely.

After the first restart you will also see:

| Entity | What it shows |
|---|---|
| Enabled | On means the rounds are allowed. Off skips them. |
| Last round | When the last scheduled round was checked. Skipped, all clear, and action rounds are all recorded. The result is in the entity attributes. The card reads as time since that round. |
| Last activity | When a monitored light, motion sensor, or door last changed. The card reads as time since that change. |
| Last active device | Which of those devices changed. |

These times are remembered across a Home Assistant restart. Turning lights off during a round does not count as activity.

---

## Install

### HACS

1. **HACS → Integrations → ⋮ → Custom repositories**
2. URL: `https://github.com/heatvent/ha-night-watchman` · Category: **Integration**
3. Download **Night Watchman**, then **restart** Home Assistant
4. **Settings → Devices & services → Add integration → Night Watchman**

HACS follows **GitHub Releases** (`v1.0.17`, …), not the tip of `main`. After a release, use **⋮ → Update information** if the update is slow to appear.

### Manual

Copy only `custom_components/night_watchman` into your Home Assistant `custom_components` folder. Do not copy the rest of this repo, and do not rename the folder.

---

## Setup

| Setting | What it does |
|---|---|
| First round / Last round | When rounds are allowed. The default is 1:00 AM through 5:00 AM, including both times. |
| Minutes between rounds | How often a round is attempted inside that window. The default is 60, so a 1:00 start runs at 1, 2, 3, 4, and 5. |
| Quiet period | How long every monitored device must stay unchanged before a round proceeds. The default is 45 minutes. |
| Notify service | Where notices go. One name, or several separated by commas, for example `phones_group` or `phones_group, tablet`. Leave off the `notify.` prefix. A notice is sent after every round. |
| Monitored Lights | A recent on or off skips the round. These lights are also turned off when **Turn Off All Monitored Lights** is selected. |
| Monitored Motion Sensors | A recent motion change skips the round. Indoor cameras belong here if you want them counted. |
| Monitored Doors | A recent open or close skips the round. This list does not lock or close anything. |
| Turn Off All Monitored Lights | Turns off the monitored lights when a round runs. |
| Lights to Keep On | Bedrooms, lamps, and anything that must stay on even when the box above is selected. |
| Other Lights/Devices to Turn Off | Extra lights or switches, such as a coffee maker or 3D printer. Leave washer, dryer, and power strips off this list. |
| Doors to Lock | Up to four lock and contact pairs. The lock runs only when that contact is closed and the lock is unlocked. An open contact is reported and left unlocked. |
| People Who Must Be Away | Optional. When every person here is away, a secure round runs after the quiet period. Leave empty to keep Night Watchman night-only. |
| Presence Simulation Switch | Optional. Turned on after the first successful away secure round. Later rounds skip lights and only check doors. Turned off when someone comes home. |

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
