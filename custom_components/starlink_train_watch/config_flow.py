"""UI setup using Home Assistant's home coordinates."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, DEFAULTS


def schema(values):
    return vol.Schema({vol.Required(k, default=values.get(k, v)): vol.Coerce(float if k in ('min_elevation', 'max_sun_altitude') else int) for k,v in DEFAULTS.items()})


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
            if not valid(user_input):
                errors['base'] = 'invalid_options'
            elif self._async_current_entries():
                return self.async_abort(reason='single_instance_allowed')
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
            if valid(user_input):
                return self.async_create_entry(title='', data=user_input)
            errors['base'] = 'invalid_options'
        values = DEFAULTS | self.config_entry.data | self.config_entry.options
        return self.async_show_form(step_id='init', data_schema=schema(user_input or values), errors=errors)
