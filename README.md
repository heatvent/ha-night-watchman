# Night Watchman

Home Assistant integration that runs nighttime rounds. You choose the devices in the integration, the same way Presence Simulation lets you choose its lights.

A round is skipped when any activity device changed during the quiet period. Current on/off is not activity. Switches that stay on, such as a washer or a power strip, should not be selected as activity devices.

When a round does run, selected lights that are still on are turned off. Each lock runs only when its paired door contact is closed. Report-only contacts are included in the phone message and are not locked or closed.

## Install with HACS

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/heatvent/ha-night-watchman` as an **Integration**.
3. Download **Night Watchman**.
4. Restart Home Assistant.
5. Go to **Settings**, **Devices & services**, **Add integration**, and choose **Night Watchman**.

Set the first and last round, the minutes between rounds, the quiet period, the notification service, the activity devices, and the lights to turn off. Then add a **Lock if the door is closed** entry for each lock, and a **Report if open** entry for contacts that should only be mentioned.

## Updates

Publish a GitHub release whose tag is `v` plus the version in `custom_components/night_watchman/manifest.json`. For version `1.0.0`, the tag is `v1.0.0`. Bump that version and the tag together for each update. HACS then offers the release. Restart Home Assistant after installing it.
