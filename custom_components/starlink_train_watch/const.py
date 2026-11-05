"""Constants for the experimental integration."""
DOMAIN = 'starlink_train_watch'
DEFAULTS = {'min_elevation': 25, 'max_sun_altitude': -6, 'max_age_days': 7, 'max_span_seconds': 180, 'min_satellites': 3}
PLATFORMS = ['sensor', 'binary_sensor', 'calendar']
