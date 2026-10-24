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

A round proceeds when motion sensors and doors have been quiet. A light that is simply left on does not count. A recent light change does not skip locks: those lights stay on and the notice names them. Recent motion or a door open or close skips the whole round, and the next scheduled round tries again.

### What a secure round does

- Turns off monitored lights that are still on (when that checkbox is selected), plus any extra lights or switches you picked, such as a coffee maker. The entity must leave `on` before that counts. A light that stays on is **Turn off failed**.
- Leaves **Lights to Keep On** alone (bedrooms, lamps). Recent light activity also leaves lights on for that round.
- Locks each selected door when its contact is closed and the lock is not already locked. Jammed, unlocking, unknown, and unavailable locks are tried. A lock already moving to locked is waited on.
- Calls lock once, waits, then tries one more time. Success is the lock entity reporting **locked**. Anything else is **Lock failed** and includes the state when it was not simply unlocked.
- Reports an open door in the notice and does not lock it. Nothing is closed. A lock whose contact is unavailable is a lock failure unless that lock already reports locked.
- Never locks the garage overhead door (refused by name).

### Night rounds (clock)

By default, rounds run inside a night window. The defaults are 1:00 AM through 5:00 AM, every 60 minutes, so attempts land at 1, 2, 3, 4, and 5.

A night round notifies you immediately only when something needs attention: a lock failed, a door is still open, a turn-off failed, or the round itself failed. Skipped and all-clear rounds wait. After the last slot (5:00 AM with the defaults) you get one **Night summary** of every round that ran. That log is kept across a restart inside the same night window. If everyone was away and no night round ran, the summary says so.

### Away when everyone leaves (optional)

Off by default, so other home and away automations are left alone.

When **Secure When Everyone Is Away** is on:

1. You pick the people who must all be away. Home and unknown block the away path. Any other person state counts as away, including a named zone such as Work.
2. When the last of those people leaves, Night Watchman waits out the quiet period. If Home Assistant restarts and the house is already quiet, it waits only the time still remaining (at least one minute), not a fresh full period.
3. It runs **one** secure round. Motion or a door change skips it. A recent light change also skips this away round, unlike a night round.
4. A skip waits for the remaining quiet time and tries again. A lock failure or a door left open does not count as secured, does not arm the alarm, and does not start Presence Simulation. It retries in 15 minutes and does not repeat the same notice.
5. After a round with no open door and no failed lock, if you set an alarm panel, that panel is armed (default mode: Away). Arming is confirmed before the notice is sent.
6. After that same successful round, if you set a [Presence Simulation](https://github.com/slashback100/presence_simulation) switch, that switch is turned **on**.
7. When someone comes home, the alarm is disarmed only if Night Watchman armed it. That fact is remembered across a restart, so a manual Away arm is left alone. Presence Simulation is turned **off**.

There is no daytime clock for this path. People leaving is the trigger. While the house is already secured by this away path — Night Watchman finished a successful round, the quiet wait is in progress, Presence Simulation is on, or the selected alarm is armed in the chosen mode — night rounds are skipped so those systems can keep control.

Night Watchman does not send an alarm code. If your panel requires a code for service calls, allow code-less arm and disarm in that alarm integration. Prefer **Arm Mode: Away** so a normal night or home arm is not treated as the away path. An alarm you armed yourself is not disarmed when someone comes home.

### Notices

Away rounds and the **Run round** button send a notice every time. Night rounds send one immediately only for a problem. The rest of the night is one summary after the last slot.

| Notice | Meaning |
|---|---|
| Night summary | After the last night slot. One line per round, such as `1:00 AM: All clear.` |
| Skipped | The house was still active. The recent devices are named. On the night path this waits for the summary. |
| All clear | The round ran and nothing needed locking, turning off, or reporting. On the night path this waits for the summary. |
| Actions | What changed: each lock, light or switch, and any door still open. Includes lights left on because of recent light activity. |
| Lock failed | The lock did not report locked, including jammed or unknown, or its contact was unavailable. Night rounds send this immediately. |
| Turn off failed | A light or switch was on and stayed on. Night rounds send this immediately. |
| Alarm armed / disarmed | Sent when the optional away alarm is armed after a successful secure round, or disarmed when someone comes home and Night Watchman had armed it. |
| Alarm arm failed | The panel did not reach the expected armed state. An open door or a failed lock prevents the arm attempt. |
| Presence simulation started / stopped | Sent when that switch is turned on after a successful away round, or turned off when someone comes home. |

Use one notify service or entity name, or several separated by commas, for example `phones_group` or `phones_group, email_group`. Leave off the `notify.` prefix. Legacy services (phones, Echos, TVs) and notify entities (SMTP Email group) are both supported.

### Pause and status

| Entity | What it shows |
|---|---|
| Enabled | On allows rounds. Off cancels a pending away quiet wait and skips night and away securing without removing the integration. Turning it back on while everyone is still away starts a new quiet wait. |
| Run round | Runs locks and lights now. It does not wait for the quiet period, and it always sends a notice. It does nothing useful while Enabled is off, other than saying Night Watchman is paused. |
| Last round | When the last round was checked (night, away, or manual). The result text is in the attributes. |
| Last activity | When a monitored light, motion sensor, or door last changed. |
| Last active device | Which of those devices changed. |

These times are remembered across a Home Assistant restart. Lights and locks Night Watchman itself changes during a round do not count as activity for the quiet check. A state refresh after startup that Night Watchman did not see does not count either. A change from before startup still does.

---

## Install

### HACS

Night Watchman is in the default HACS list.

1. **HACS → Integrations** and search for **Night Watchman**
2. Download it, then **restart** Home Assistant
3. **Settings → Devices & services → Add integration → Night Watchman**

To install before it shows in that list, add a custom repository: **HACS → Integrations → ⋮ → Custom repositories**, URL `https://github.com/heatvent/ha-night-watchman`, category **Integration**.

HACS follows **GitHub Releases** (`v1.0.22`, …), not the tip of `main`. After a release, use **⋮ → Update information** if the update is slow to appear.

The HACS store list may show a placeholder icon. That is a HACS limitation. After install, the icon appears under **Settings → Devices & services**.

### Manual

Copy only `custom_components/night_watchman` into your Home Assistant `custom_components` folder. Do not copy the rest of this repo, and do not rename the folder.

---

## Setup

| Setting | What it does |
|---|---|
| First round / Last round | Night window when clock rounds are allowed. Default 1:00 AM through 5:00 AM. |
| Minutes between rounds | How often a night round is attempted inside that window. Default 60. |
| Quiet period | How long monitored devices must stay unchanged before a round proceeds. Default 45 minutes. Also used after the last person leaves for the away path. |
| Notify service | Where notices go. One name or a comma-separated list of notify services or entities. Leave off `notify.`. Example: `phones_group, email_group`. |
| Monitored Lights | A recent on or off leaves lights on for that night round and is named in the result. Motion and doors still skip the round. Also turned off when **Turn Off All Monitored Lights** is selected and the house is quiet. |
| Monitored Motion Sensors | A recent motion change skips the round. Indoor cameras belong here if you want them counted. |
| Monitored Doors | A recent open or close skips the round. This list does not lock or close anything. |
| Turn Off All Monitored Lights | Turns off the monitored lights when a secure round runs. |
| Lights to Keep On | Stays on even when the box above is selected. |
| Other Lights/Devices to Turn Off | Extra lights or switches (coffee maker, 3D printer). Do not add strips that should stay on. |
| Doors to Lock | Up to four lock and contact pairs. Lock only if unlocked and the contact is closed. |
| Secure When Everyone Is Away | Off by default. When on, people leaving triggers one secure round after the quiet period. |
| People Who Must Be Away | Used only when the box above is on. Home or unknown blocks the away path. A named zone counts as away. |
| Alarm Panel | Optional. Armed after a successful away secure round. Disarmed when someone comes home only if Night Watchman armed it, including after a restart. No code is sent. |
| Arm Mode | Away, Home, Night, or Vacation. Default Away. Prefer Away. Only that armed state counts as “already secured” for skipping night rounds. |
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

Each release tag is `v` plus the version in `custom_components/night_watchman/manifest.json`. Version `1.0.22` is tag `v1.0.22`. Bump both together. HACS offers the new release. Restart Home Assistant after installing it.
