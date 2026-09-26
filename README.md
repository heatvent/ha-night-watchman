# HA Night Watchman

Home Assistant custom integration that runs nighttime rounds.

You choose the devices in the integration, the same way Presence Simulation lets you choose its lights:

- Activity devices. A recent change skips the round. On/off by itself is not activity.
- Lights to turn off when a round runs.
- A lock paired with the door contact that must be closed first.
- Contacts that are only reported when open.

Rounds run from a start time through an end time, on the interval you set.
