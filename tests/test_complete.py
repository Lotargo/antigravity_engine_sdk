import threading

import pytest

from antigravity_engine_sdk import AntigravityError


def test_complete_is_explicit_toolless_literal_contract(boundary):
    with boundary.client() as client:
        result = client.complete(
            "literal prompt", model="test-low", instructions="instruction"
        )
        assert result.text == "Native planner text\nsecond line"
        assert result.raw_output == result.text
        assert result.output_mode == "planner_text"
        start = boundary.calls("StartCascade")[-1]["body"]
        assert start["customAgentSpec"]["customAgent"]["toolNames"] == []
        sent = boundary.calls("SendUserCascadeMessage")[-1]["body"]
        assert sent["items"] == [{"text": "instruction\n\nliteral prompt"}]
        config = sent["cascadeConfig"]
        assert "finish" not in config["plannerConfig"]["toolConfig"]
        assert config["executorConfig"] == {
            "maxGeneratorInvocations": 1,
            "requireFinishTool": False,
        }
        with pytest.raises(AntigravityError, match="schema"):
            client.complete("literal", model="test-low", schema={"type": "object"})
        boundary.set("no_finish")
        with pytest.raises(AntigravityError) as error:
            client.chat("strict", model="test-low")
        assert error.value.code == "incompatible_format"


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("failed", "provider_error"),
        ("blocked", "content_rejected"),
        ("retired", "model_unavailable"),
        ("pending_finish", "incompatible_format"),
    ],
)
def test_complete_rejects_failed_or_partial_trajectories(boundary, scenario, code):
    boundary.set(scenario)
    with boundary.client() as client, pytest.raises(AntigravityError) as error:
        client.complete("test", model="test-low")
    assert error.value.code == code


def test_complete_cancel_and_recover(boundary):
    boundary.set("running")
    stop = threading.Event()
    with boundary.client() as client:
        with pytest.raises(AntigravityError) as error:
            client.complete(
                "test",
                model="test-low",
                cancelled=stop.is_set,
                on_progress=lambda event: (
                    stop.set() if event.phase == "trajectory" else None
                ),
            )
        assert error.value.code == "cancelled"
        assert boundary.calls("CancelCascadeInvocation")
        boundary.set("success")
        assert client.complete("test", model="test-low").text.startswith(
            "Native planner"
        )
