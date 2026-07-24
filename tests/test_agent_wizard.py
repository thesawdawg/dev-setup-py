from __future__ import annotations

import pytest

from dev_setup.agent import config as agent_config
from dev_setup.agent import wizard
from dev_setup.agent.config import AgentConfig


@pytest.fixture()
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "agent.yaml"
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", path)
    return path


class FakePrompts:
    """Scripts the ui.* calls the wizard makes, in order."""

    def __init__(self, monkeypatch, *, texts=None, selects=None, confirms=None):
        self.texts = list(texts or [])
        self.selects = list(selects or [])
        self.confirms = list(confirms or [])
        monkeypatch.setattr(wizard.ui, "text_input", self._text)
        monkeypatch.setattr(wizard.ui, "select", self._select)
        monkeypatch.setattr(wizard.ui, "confirm", self._confirm)
        # Silence output helpers.
        for name in ("section", "dim", "info", "success", "warn"):
            monkeypatch.setattr(wizard.ui, name, lambda *a, **k: None)
        import contextlib

        monkeypatch.setattr(wizard.ui, "spinner", lambda *a, **k: contextlib.nullcontext())

    def _text(self, prompt, default="", required=False):
        return self.texts.pop(0) if self.texts else default

    def _select(self, prompt, choices):
        return self.selects.pop(0) if self.selects else choices[0]

    def _confirm(self, prompt, default=False):
        return self.confirms.pop(0) if self.confirms else default


def fake_models(monkeypatch, capabilities: dict[str, list[str]]):
    """Make OllamaClient return a scripted set of models and capabilities."""

    def list_models(self):
        return list(capabilities)

    def caps(self, model):
        return capabilities[model]

    monkeypatch.setattr(wizard.OllamaClient, "list_models", list_models)
    monkeypatch.setattr(wizard.OllamaClient, "capabilities", caps)


# -- config save/exists ----------------------------------------------------------


def test_exists_reflects_the_file(config_path):
    assert agent_config.exists() is False
    config_path.write_text("version: 1\nmodel: x\n")
    assert agent_config.exists() is True


def test_save_writes_a_loadable_file(config_path):
    cfg = AgentConfig(model="gemma4:latest", host="http://box:11434", think=True)
    agent_config.save(cfg)
    reloaded = agent_config.load()
    assert reloaded.model == "gemma4:latest"
    assert reloaded.host == "http://box:11434"
    assert reloaded.think is True


def test_save_omits_untouched_defaults(config_path):
    agent_config.save(AgentConfig(model="m", host="h"))
    text = config_path.read_text()
    # num_ctx/temperature kept their defaults, so they should not be written out.
    assert "num_ctx" not in text
    assert "temperature" not in text
    # model and host are always recorded, even at default, as the point of the file.
    assert "model:" in text
    assert "host:" in text


def test_saved_file_has_a_header_comment(config_path):
    agent_config.save(AgentConfig(model="m", host="h"))
    assert config_path.read_text().startswith("# devstuff agent configuration.")


# -- should_run ------------------------------------------------------------------


def test_should_run_only_on_first_interactive_run(config_path):
    assert wizard.should_run(interactive=True) is True
    assert wizard.should_run(interactive=False) is False  # no terminal
    config_path.write_text("version: 1\nmodel: x\n")
    assert wizard.should_run(interactive=True) is False  # already configured


# -- the wizard flow -------------------------------------------------------------


def test_wizard_writes_the_chosen_model_and_host(config_path, monkeypatch):
    fake_models(monkeypatch, {"gemma4:latest": ["tools"], "nomic:latest": ["completion"]})
    FakePrompts(
        monkeypatch,
        texts=["http://localhost:11434"],  # host
        selects=["gemma4:latest"],          # model
        confirms=[False],                    # think
    )
    cfg = wizard.run()
    assert cfg.model == "gemma4:latest"
    assert cfg.host == "http://localhost:11434"
    assert agent_config.exists()
    assert agent_config.load().model == "gemma4:latest"


def test_wizard_lists_only_tool_capable_models(config_path, monkeypatch):
    fake_models(
        monkeypatch,
        {"good:latest": ["tools"], "embed:latest": ["completion"], "good2:latest": ["tools"]},
    )
    captured = {}

    def capture_select(prompt, choices):
        captured["choices"] = choices
        return choices[0]

    FakePrompts(monkeypatch, texts=["http://localhost:11434"], confirms=[False])
    monkeypatch.setattr(wizard.ui, "select", capture_select)

    wizard.run()
    assert "good:latest" in captured["choices"]
    assert "good2:latest" in captured["choices"]
    assert "embed:latest" not in captured["choices"]  # no tools capability


def test_wizard_falls_back_to_manual_entry_when_no_capable_models(config_path, monkeypatch):
    fake_models(monkeypatch, {"embed:latest": ["completion"]})
    # No select offered; the model comes from a text prompt instead.
    FakePrompts(
        monkeypatch,
        texts=["http://localhost:11434", "gemma4:latest"],
        confirms=[False],
    )
    cfg = wizard.run()
    assert cfg.model == "gemma4:latest"


def test_wizard_normalizes_a_bare_model_name(config_path, monkeypatch):
    fake_models(monkeypatch, {})
    FakePrompts(monkeypatch, texts=["http://localhost:11434", "gemma4"], confirms=[False])
    assert wizard.run().model == "gemma4:latest"


def test_wizard_survives_an_unreachable_daemon(config_path, monkeypatch):
    """No models listable — the wizard must still let the user save a config."""
    from dev_setup.agent.ollama import OllamaUnavailable

    def boom(self):
        raise OllamaUnavailable("refused")

    monkeypatch.setattr(wizard.OllamaClient, "list_models", boom)
    FakePrompts(
        monkeypatch,
        texts=["http://localhost:11434", "gemma4:latest"],
        confirms=[False],
    )
    cfg = wizard.run()
    assert cfg.model == "gemma4:latest"


def test_manual_choice_in_the_menu_prompts_for_a_name(config_path, monkeypatch):
    fake_models(monkeypatch, {"good:latest": ["tools"]})
    FakePrompts(
        monkeypatch,
        texts=["http://localhost:11434", "custom:latest"],
        selects=[wizard._MANUAL],
        confirms=[False],
    )
    assert wizard.run().model == "custom:latest"


def test_rerun_seeds_prompts_from_the_existing_config(config_path, monkeypatch):
    fake_models(monkeypatch, {"gemma4:latest": ["tools"]})
    existing = AgentConfig(model="gemma4:latest", host="http://box:11434", think=True)

    seen = {}

    def capture_text(prompt, default="", required=False):
        seen.setdefault("host_default", default)
        return default

    monkeypatch.setattr(wizard.ui, "text_input", capture_text)
    monkeypatch.setattr(wizard.ui, "select", lambda p, c: c[0])
    monkeypatch.setattr(wizard.ui, "confirm", lambda p, default=False: default)
    for name in ("section", "dim", "info", "success", "warn"):
        monkeypatch.setattr(wizard.ui, name, lambda *a, **k: None)
    import contextlib

    monkeypatch.setattr(wizard.ui, "spinner", lambda *a, **k: contextlib.nullcontext())

    wizard.run(existing)
    assert seen["host_default"] == "http://box:11434"
