"""Constants for the Urmet 1067 integration."""

from datetime import timedelta

DOMAIN = "urmet_1067"

CONF_PIN = "pin"
CONF_IMPORT_SENSORS = "import_sensors"
CONF_ZONES = "zones"  # {str(zone_id): name}, captured at setup
CONF_SENSORS = "sensors"  # [{"id": int, "name": str, "zones": [int]}], captured at setup
CONF_MAC = "mac"
CONF_AWAY_ZONES = "away_zones"  # [zone_id, ...], default all
CONF_HOME_ZONES = "home_zones"
CONF_NIGHT_ZONES = "night_zones"
CONF_VACATION_ZONES = "vacation_zones"
CONF_ZONE_PANELS = "zone_panels"  # one alarm entity per zone, default on

# ponytail: the panel accepts ONE web session at a time; polling more often
# than this makes the official app/browser unusable.
UPDATE_INTERVAL = timedelta(seconds=60)
# Consecutive "another user is connected" polls tolerated before unavailable.
MAX_BUSY_POLLS = 5
# Panel clock vs HA clock tolerance when deciding an alarm happened after arming.
CLOCK_SLACK = timedelta(minutes=2)
