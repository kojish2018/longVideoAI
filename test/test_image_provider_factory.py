from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepinfra_client import DeepInfraClient
from image_provider_factory import make_image_client
from pollinations_client import PollinationsClient


def test_make_image_client_defaults_to_pollinations() -> None:
    config = {
        "apis": {
            "pollinations": {"width": 640, "height": 360},
        }
    }
    client = make_image_client(config)
    assert isinstance(client, PollinationsClient)


def test_make_image_client_selects_deepinfra() -> None:
    config = {
        "apis": {
            "image_provider": "deepinfra",
            "deepinfra": {
                "api_token": "dummy-token",
            },
        }
    }
    client = make_image_client(config)
    assert isinstance(client, DeepInfraClient)
    