import pytest

from antigravity_engine_sdk import AntigravityError


@pytest.mark.parametrize("scenario", ["success", "image_inline"])
def test_images_survive_engine_cleanup_and_save_without_overwrite(
    boundary, tmp_path, scenario
):
    boundary.set(scenario)
    client = boundary.client()
    result = client.generate_image(
        "a blue cup", planner="test-low", expected_model="image-test"
    )
    client.close()
    assert len(result.images) == 1
    artifact = result.images[0]
    assert artifact.mime_type == "image/png" and artifact.model == "image-test"
    assert artifact.data.startswith(b"\x89PNG")
    target = artifact.save(tmp_path / "image.png")
    assert target.read_bytes() == artifact.data
    with pytest.raises(FileExistsError):
        artifact.save(target)
    start = boundary.calls("StartCascade")[0]["body"]
    assert start["customAgentSpec"]["customAgent"]["toolNames"] == [
        "generate_image",
        "finish",
    ]


@pytest.mark.parametrize(
    "scenario, code",
    [
        ("image_missing", "image_missing"),
        ("image_mismatch", "image_model_mismatch"),
        ("image_external", "image_missing"),
    ],
)
def test_image_claims_and_external_uris_are_not_artifacts(boundary, scenario, code):
    boundary.set(scenario)
    with pytest.raises(AntigravityError) as caught:
        boundary.client().generate_image(
            "a blue cup", planner="test-low", expected_model="image-test"
        )
    assert caught.value.code == code
