"""视频能力映射单测。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.contracts.video_generation import VideoGenerationInput
from app.core.integrations.video_capabilities import (
    VideoModelCapability,
    clear_video_model_capability_overrides,
    derive_provider_size,
    register_video_model_capability,
    resolve_video_capability,
    validate_video_options,
)


def test_explicit_ratio_maps_to_provider_size_without_legacy_size_input() -> None:
    """The current business contract requires an explicit ratio and does not infer it from a removed size field."""
    assert derive_provider_size(provider="openai",model=None,ratio="16:9")=="1280x720"
    assert derive_provider_size(provider="openai",model=None,ratio="9:16")=="720x1280"
    with pytest.raises(ValidationError):
        VideoGenerationInput.model_validate({"prompt":"authored","size":"1920x1080"})


def test_resolve_video_capability_prefers_longest_prefix() -> None:
    """Explicit compatibility capability overrides use the most specific registered model prefix."""
    clear_video_model_capability_overrides(provider="openai")
    register_video_model_capability(
        provider="openai",
        model_prefix="gpt-video",
        capability=VideoModelCapability(supports_seed=False),
    )
    register_video_model_capability(
        provider="openai",
        model_prefix="gpt-video-pro",
        capability=VideoModelCapability(supports_seed=True, supports_watermark=False),
    )
    try:
        cap = resolve_video_capability(provider="openai", model="gpt-video-pro-1")
        assert cap.supports_seed is True
        assert cap.supports_watermark is False
    finally:
        clear_video_model_capability_overrides(provider="openai")


def test_validate_video_options_rejects_capability_mismatch() -> None:
    """A valid current ratio cannot authorize a seed rejected by the selected capability."""
    clear_video_model_capability_overrides(provider="volcengine")
    register_video_model_capability(
        provider="volcengine",
        model_prefix="seedream-video",
        capability=VideoModelCapability(supports_seed=False),
    )
    try:
        inp = VideoGenerationInput(prompt="test",model="seedream-video-v1",ratio="16:9",seed=7)
        with pytest.raises(ValueError) as exc_info:
            validate_video_options(provider="volcengine", model=inp.model, input_=inp)
        assert "seed is not supported" in str(exc_info.value)
    finally:
        clear_video_model_capability_overrides(provider="volcengine")
