from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deepinfra_client  # noqa: E402
from deepinfra_client import DeepInfraClient  # noqa: E402


class DummyResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:  # pragma: no cover
        return None


def test_fetch_strips_data_uri_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample_bytes = b"fake-image-bytes"
    encoded_body = base64.b64encode(sample_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{encoded_body}"

    response_payload = {
        "images": [data_uri],
    }

    def fake_post(*args: Any, **kwargs: Any) -> DummyResponse:
        return DummyResponse(response_payload)

    monkeypatch.setattr(deepinfra_client.requests, "post", fake_post)

    client = DeepInfraClient(
        {
            "apis": {
                "deepinfra": {
                    "api_token": "test-token",
                }
            }
        }
    )

    output_path = tmp_path / "image.jpg"

    result = client.fetch("test prompt", output_path)

    assert result == output_path
    assert output_path.read_bytes() == sample_bytes


def test_profile_merging_prioritises_profile_over_base() -> None:
    config: dict[str, Any] = {
        "apis": {
            "deepinfra": {
                "api_token": "token-123",
                "base": {
                    "guidance_scale": 0.5,
                    "num_inference_steps": 3,
                    "width": 1152,
                    "height": 648,
                    "scheduler": "Euler",
                },
                "profiles": {
                    "default": {
                        "model": "stabilityai/sdxl-turbo",
                        "num_inference_steps": 4,
                        "width": 1920,
                        "height": 1080,
                    },
                    "flux_schnell": {
                        "model": "black-forest-labs/FLUX-1-schnell",
                        "width": 1920,
                        "height": 1080,
                        "num_inference_steps": 2,
                        "guidance_scale": 0.0,
                        "scheduler": None,
                    },
                },
                "profile": "flux_schnell",
            }
        }
    }

    client = DeepInfraClient(config)

    assert client.model == "black-forest-labs/FLUX-1-schnell"
    assert client.width == 1920  # profile値がbaseより優先
    assert client.height == 1080
    assert client.num_inference_steps == 1
    assert client.guidance_scale == 0.0
    assert client.scheduler == ""  # None指定で空に戻る
    assert client.active_profile == "flux_schnell"


def test_profile_default_fallback_is_used_when_no_override() -> None:
    config: dict[str, Any] = {
        "apis": {
            "deepinfra": {
                "api_token": "token-abc",
                "base": {
                    "num_inference_steps": 4,
                    "width": 1024,
                    "height": 1024,
                },
                "profiles": {
                    "default": {
                        "model": "stabilityai/sdxl-turbo",
                    }
                },
                "default_profile": "default",
            }
        }
    }

    client = DeepInfraClient(config)

    assert client.model == "stabilityai/sdxl-turbo"
    assert client.width == 1024
    assert client.height == 1024
    assert client.active_profile == "default"
