"""Validate the distributed blueprint using HA's own schemas."""
from pathlib import Path
from homeassistant.components.blueprint.models import Blueprint
from homeassistant.components.automation import config
from homeassistant.util import yaml

def test_blueprint_schema():
    path = Path(__file__).parents[1]/'blueprints/automation/starlink_train_watch/notify.yaml'
    data = yaml.load_yaml(str(path))
    bp = Blueprint(data, expected_domain='automation', schema=config.AUTOMATION_BLUEPRINT_SCHEMA)
    bp.validate()
    assert 'notification_action' in bp.inputs
