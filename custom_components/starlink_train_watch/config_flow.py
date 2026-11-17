"""UI setup using Home Assistant's home coordinates."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig, NumberSelectorMode
from .const import DOMAIN, DEFAULTS


INTEGER_OPTIONS = ('max_age_days', 'max_span_seconds', 'min_satellites')
# (minimum, maximum, step); min_satellites has no practical upper limit.
LIMITS = {'min_elevation': (0, 85, 'any'), 'max_sun_altitude': (-18, -1, 'any'),
          'max_age_days': (1, 14, 1), 'max_span_seconds': (30, 600, 1), 'min_satellites': (3, 10000, 1)}


def normalize(data):
    """Turn whole-number floats from the number boxes (4.0) into ints; leave 3.7 alone to be rejected."""
    out = dict(data)
    for key in INTEGER_OPTIONS:
        if isinstance(out.get(key), float) and out[key].is_integer():
            out[key] = int(out[key])
    return out


def schema(values):
    fields = {}
    for key, default in DEFAULTS.items():
        low, high, step = LIMITS[key]
        selector = NumberSelector(NumberSelectorConfig(min=low, max=high, step=step, mode=NumberSelectorMode.BOX))
        fields[vol.Required(key, default=values.get(key, default))] = selector
    return vol.Schema(fields)


def valid(data):
    try:
        minimum = data.get('min_satellites', DEFAULTS['min_satellites'])
        return (not isinstance(minimum, bool) and isinstance(minimum, int) and minimum >= 3
                and 0 <= data['min_elevation'] <= 85 and -18 <= data['max_sun_altitude'] <= -1
                and 1 <= data['max_age_days'] <= 14 and 30 <= data['max_span_seconds'] <= 600)
    except (KeyError, TypeError):
        return False


class TrainConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one observer at the HA home location."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = normalize(user_input)
            if not valid(user_input):
                errors['base'] = 'invalid_options'
            else:
                return self.async_create_entry(title='Starlink Train Watch', data=user_input)
        return self.async_show_form(step_id='user', data_schema=schema(user_input or DEFAULTS), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TrainOptionsFlow()


class TrainOptionsFlow(config_entries.OptionsFlow):
    """Adjust viewing filters without editing YAML."""
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = normalize(user_input)
            if valid(user_input):
                return self.async_create_entry(title='', data=user_input)
            errors['base'] = 'invalid_options'
        values = DEFAULTS | self.config_entry.data | self.config_entry.options
        return self.async_show_form(step_id='init', data_schema=schema(user_input or values), errors=errors)
