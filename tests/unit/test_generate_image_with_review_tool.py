import json
import types


def test_generate_image_with_review_converges(monkeypatch, tmp_path):
    """
    Unit-test the control-flow only (no network):
    - 1st generate succeeds
    - review fails with edit_prompt
    - 2nd generate succeeds
    - review passes
    """
    import src.mcp_server as mcp_server

    img1 = tmp_path / "out.png"
    img1.write_bytes(b"fake")
    img2 = tmp_path / "out2.png"
    img2.write_bytes(b"fake2")

    calls = {"gen": 0, "review": 0}

    def fake_generate_image(*, prompt, input_image_path=None, output_dir=None, output_filename=None, preview=False, model_name=None):
        calls["gen"] += 1
        out = img1 if calls["gen"] == 1 else img2
        return json.dumps(
            {
                "success": True,
                "output_path": str(out),
                "session_output_path": str(out),
            }
        )

    def fake_review(*, image_path, creation_prompt, model_name):
        calls["review"] += 1
        if calls["review"] == 1:
            return {
                "passes": False,
                "issues": [{"type": "typo", "detail": "bad text"}],
                "edit_prompt": "Fix typo: change 'OpeanAI' to 'OpenAI'.",
            }
        return {"passes": True, "issues": [], "edit_prompt": ""}

    monkeypatch.setattr(mcp_server, "generate_image", fake_generate_image)
    monkeypatch.setattr(mcp_server, "_review_generated_image_against_prompt", fake_review)

    out = mcp_server.generate_image_with_review(
        prompt="Make an infographic with OpenAI spelled correctly.",
        output_dir=str(tmp_path),
        output_filename="final.png",
        preview=False,
        model_name="gemini-3-pro-image-preview",
        max_attempts=3,
    )
    obj = json.loads(out)
    assert obj["success"] is True
    assert "review_history" in obj
    assert calls["gen"] == 2
    assert calls["review"] == 2


def test_generate_image_with_review_stops_on_repeated_edit_prompt(monkeypatch, tmp_path):
    import src.mcp_server as mcp_server

    img = tmp_path / "out.png"
    img.write_bytes(b"fake")

    def fake_generate_image(*, prompt, input_image_path=None, output_dir=None, output_filename=None, preview=False, model_name=None):
        return json.dumps({"success": True, "output_path": str(img), "session_output_path": str(img)})

    def fake_review(*, image_path, creation_prompt, model_name):
        return {
            "passes": False,
            "issues": [{"type": "typo", "detail": "still bad"}],
            "edit_prompt": "Fix the typo X.",
        }

    monkeypatch.setattr(mcp_server, "generate_image", fake_generate_image)
    monkeypatch.setattr(mcp_server, "_review_generated_image_against_prompt", fake_review)

    out = mcp_server.generate_image_with_review(
        prompt="Infographic.",
        output_dir=str(tmp_path),
        output_filename="final.png",
        preview=False,
        model_name="gemini-3-pro-image-preview",
        max_attempts=3,
    )
    obj = json.loads(out)
    assert obj.get("success") is True
    assert obj.get("qc_converged") is False
    assert len(obj.get("review_history") or []) >= 1

