"""Constants for Night Watchman."""

DOMAIN = "night_watchman"

CONF_START = "start"
CONF_END = "end"
CONF_INTERVAL = "interval"
CONF_QUIET_MINUTES = "quiet_minutes"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_ACTIVITY_ENTITIES = "activity_entities"
CONF_ACTIVITY_LIGHTS = "activity_lights"
CONF_ACTIVITY_MOTION = "activity_motion"
CONF_ACTIVITY_DOORS = "activity_doors"
CONF_MONITOR_ALL = "monitor_all"
CONF_EXCLUDE_LIGHTS = "exclude_lights"
CONF_EXCLUDE_MOTION = "exclude_motion"
CONF_EXCLUDE_DOORS = "exclude_doors"
CONF_INCLUDE_ACTIVITY_LIGHTS = "include_activity_lights"
CONF_KEEP_ON_LIGHTS = "keep_on_lights"
CONF_TURN_OFF_ENTITIES = "turn_off_entities"
CONF_LOCK_ENTITY = "lock_entity"
CONF_CONTACT_ENTITY = "contact_entity"
CONF_DOORS_TO_LOCK = "doors_to_lock"
CONF_AWAY_ENABLED = "away_enabled"
CONF_AWAY_PEOPLE = "away_people"
CONF_PRESENCE_SIMULATION = "presence_simulation_switch"
CONF_AWAY_ALARM = "away_alarm"
CONF_AWAY_ALARM_MODE = "away_alarm_mode"

AWAY_ALARM_MODES = ("away", "home", "night", "vacation")
DEFAULT_AWAY_ALARM_MODE = "away"

SUBENTRY_LOCK = "lock_rule"
SUBENTRY_REPORT = "report_open"

LOCK_SLOTS = 4
PLATFORMS = ["sensor", "switch"]
