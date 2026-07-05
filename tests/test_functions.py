from __future__ import annotations

import json
from importlib import resources

import pytest
import yaml

from dev_setup import function_runner as runner
from dev_setup import functions_catalog as catalog
from dev_setup import functions_registry as registry
from dev_setup.function_runner import ParamResolutionError
from dev_setup.functions_catalog import CatalogError
from dev_setup.functions_registry import FunctionDef, FunctionParam


@pytest.fixture()
def isolated_catalog(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setattr(catalog, "CONFIG_DIR", config)
    monkeypatch.setattr(catalog, "USER_CATALOG_PATH", config / "functions.yaml")
    registry._registry.clear()
    registry._order.clear()
    registry._initialized = False
    yield config
    registry._registry.clear()
    registry._order.clear()
    registry._initialized = False


# -- catalog validation ----------------------------------------------------------


def test_bundled_catalog_loads_ssh_agent_key(isolated_catalog):
    fns = catalog.load_bundled_catalog()
    assert "ssh-agent-key" in fns
    assert fns["ssh-agent-key"]["type"] == "shell-eval"
    assert fns["ssh-agent-key"]["register"] == "bashrc"


def test_bundled_catalog_loads_validation_and_web_dev_functions(isolated_catalog):
    fns = catalog.load_bundled_catalog()
    assert fns["validate-docker-compose"]["category"] == "validation"
    assert fns["validate-yaml"]["category"] == "validation"
    assert fns["acc-check"]["category"] == "web-dev"
    assert fns["aws-saml-reauth"]["category"] == "web-dev"


def test_category_defaults_to_custom(isolated_catalog):
    result = catalog.validate_catalog(
        {"version": 1, "functions": {"fn": {"type": "script", "script": "echo hi"}}}
    )
    assert result["fn"]["category"] == "custom"


def test_category_round_trips_through_functiondef(isolated_catalog):
    fn = registry.FunctionDef.from_dict(
        {"type": "script", "script": "echo hi", "category": "validation"}, key="fn"
    )
    assert fn.category == "validation"
    assert fn.to_dict()["category"] == "validation"


def test_user_catalog_overrides_bundled_function_in_place(isolated_catalog):
    catalog.write_user_catalog(
        {
            "ssh-agent-key": {
                "name": "Custom SSH",
                "description": "override",
                "type": "script",
                "script": "echo hi",
            },
        }
    )
    registry.reload()
    assert registry.get("ssh-agent-key").name == "Custom SSH"  # type: ignore[union-attr]


def test_invalid_type_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="type must be one of"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {"bad": {"type": "nope", "script": "echo hi"}},
            }
        )


def test_missing_script_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="must set 'script'"):
        catalog.validate_catalog(
            {"version": 1, "functions": {"bad": {"type": "script"}}}
        )


def test_register_field_rejected_for_script_type(isolated_catalog):
    with pytest.raises(CatalogError, match="'register' only applies"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {
                    "bad": {"type": "script", "script": "echo hi", "register": "bashrc"}
                },
            }
        )


def test_register_defaults_to_bashrc_for_shell_eval(isolated_catalog):
    result = catalog.validate_catalog(
        {
            "version": 1,
            "functions": {"fn": {"type": "shell-eval", "script": "echo hi"}},
        }
    )
    assert result["fn"]["register"] == "bashrc"


def test_invalid_register_value_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="register must be one of"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {
                    "bad": {"type": "shell-eval", "script": "echo hi", "register": "nope"}
                },
            }
        )


def test_param_missing_name_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="valid shell identifier"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {
                    "bad": {
                        "type": "script",
                        "script": "echo hi",
                        "params": [{"description": "no name"}],
                    }
                },
            }
        )


def test_duplicate_param_name_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="duplicate param name"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {
                    "bad": {
                        "type": "script",
                        "script": "echo hi",
                        "params": [{"name": "x"}, {"name": "x"}],
                    }
                },
            }
        )


def test_unknown_field_rejected(isolated_catalog):
    with pytest.raises(CatalogError, match="unknown field"):
        catalog.validate_catalog(
            {
                "version": 1,
                "functions": {
                    "bad": {"type": "script", "script": "echo hi", "mystery": True}
                },
            }
        )


def test_save_user_function_writes_yaml(isolated_catalog):
    catalog.save_user_function(
        "my-fn", {"name": "My Fn", "description": "", "type": "script", "script": "echo hi"}
    )
    data = yaml.safe_load(catalog.USER_CATALOG_PATH.read_text())
    assert data["functions"]["my-fn"]["name"] == "My Fn"


def test_delete_user_function(isolated_catalog):
    catalog.save_user_function(
        "my-fn", {"name": "My Fn", "description": "", "type": "script", "script": "echo hi"}
    )
    assert catalog.delete_user_function("my-fn") is True
    assert catalog.delete_user_function("my-fn") is False


# -- param resolution --------------------------------------------------------------


def _params(*names: str) -> list[FunctionParam]:
    return [FunctionParam(name=n, required=True) for n in names]


def test_resolve_params_uses_positional_args():
    values = runner.resolve_params(_params("a", "b"), ("1", "2"))
    assert values == ["1", "2"]


def test_resolve_params_fills_missing_via_prompt():
    values = runner.resolve_params(_params("a", "b"), ("1",), prompt=lambda p: "prompted")
    assert values == ["1", "prompted"]


def test_resolve_params_uses_default_when_no_prompt():
    params = [FunctionParam(name="a", required=True, default="fallback")]
    values = runner.resolve_params(params, ())
    assert values == ["fallback"]


def test_resolve_params_raises_when_required_missing_and_no_prompt():
    with pytest.raises(ParamResolutionError, match="a, b"):
        runner.resolve_params(_params("a", "b"), ())


def test_resolve_params_optional_without_default_resolves_empty():
    params = [FunctionParam(name="a", required=False)]
    assert runner.resolve_params(params, ()) == [""]


def test_resolve_params_explicit_empty_string_counts_as_missing():
    """An explicitly empty positional arg must not satisfy a required param — this was
    the reported bug: `dev-setup run key ""` ran successfully with an empty value."""
    with pytest.raises(ParamResolutionError, match="a"):
        runner.resolve_params(_params("a"), ("",))


def test_resolve_params_explicit_empty_string_falls_through_to_prompt():
    values = runner.resolve_params(_params("a"), ("",), prompt=lambda p: "prompted")
    assert values == ["prompted"]


def test_resolve_params_explicit_empty_string_falls_through_to_default():
    params = [FunctionParam(name="a", required=True, default="fallback")]
    assert runner.resolve_params(params, ("",)) == ["fallback"]


# -- rendering ----------------------------------------------------------------------


def _fn(**kwargs) -> FunctionDef:
    kwargs.setdefault("key", "demo")
    kwargs.setdefault("type", "script")
    kwargs.setdefault("script", "echo hi\n")
    return FunctionDef(**kwargs)


def test_render_eval_script_bakes_literal_values():
    fn = _fn(
        type="shell-eval",
        register="eval",
        params=[FunctionParam(name="msg", required=True)],
        script='echo "$msg"\n',
    )
    rendered = runner.render_eval_script(fn, ("hello world",))
    assert rendered == "msg='hello world'\necho \"$msg\"\n"


def test_render_eval_script_raises_on_missing_required_param():
    fn = _fn(
        type="shell-eval",
        register="eval",
        params=[FunctionParam(name="msg", required=True)],
    )
    with pytest.raises(ParamResolutionError):
        runner.render_eval_script(fn, ())


def test_render_bashrc_function_uses_positional_refs():
    fn = _fn(
        key="my-fn",
        type="shell-eval",
        register="bashrc",
        params=[FunctionParam(name="key_path", required=True)],
        script='ssh-add "$key_path"\n',
    )
    rendered = runner.render_bashrc_function(fn)
    assert rendered == (
        "my-fn() {\n"
        '  local key_path="$1"\n'
        '  if [ -z "$key_path" ]; then\n'
        '    echo "my-fn: missing required argument: key_path" >&2\n'
        "    return 1\n"
        "  fi\n"
        '  ssh-add "$key_path"\n'
        "}"
    )


def test_render_bashrc_function_no_params():
    fn = _fn(key="my-fn", type="shell-eval", register="bashrc", script="echo hi\n")
    rendered = runner.render_bashrc_function(fn)
    assert rendered == "my-fn() {\n  echo hi\n}"


def test_render_bashrc_function_optional_param_has_no_guard():
    fn = _fn(
        key="my-fn",
        type="shell-eval",
        register="bashrc",
        params=[FunctionParam(name="msg", required=False)],
        script='echo "$msg"\n',
    )
    rendered = runner.render_bashrc_function(fn)
    assert "missing required argument" not in rendered
    assert rendered == 'my-fn() {\n  local msg="$1"\n  echo "$msg"\n}'


def test_render_bashrc_function_required_with_default_has_no_guard():
    fn = _fn(
        key="my-fn",
        type="shell-eval",
        register="bashrc",
        params=[FunctionParam(name="msg", required=True, default="hi")],
        script='echo "$msg"\n',
    )
    rendered = runner.render_bashrc_function(fn)
    assert "missing required argument" not in rendered


# -- schema/code sync ---------------------------------------------------------------
# functions.schema.json is hand-maintained documentation, not loaded at runtime — these
# guard against it silently drifting from the fields/enums validate_catalog() enforces.


def _load_schema() -> dict:
    resource = resources.files("dev_setup").joinpath("functions.schema.json")
    return json.loads(resource.read_text())


def test_schema_function_fields_match_supported_fields():
    schema = _load_schema()
    documented = set(schema["definitions"]["function"]["properties"])
    assert documented == catalog.SUPPORTED_FIELDS


def test_schema_param_fields_match_supported_param_fields():
    schema = _load_schema()
    documented = set(schema["definitions"]["param"]["properties"])
    assert documented == catalog.SUPPORTED_PARAM_FIELDS


def test_schema_type_enum_matches_catalog_types():
    schema = _load_schema()
    documented = set(schema["definitions"]["function"]["properties"]["type"]["enum"])
    assert documented == catalog.TYPES


def test_schema_register_enum_matches_catalog_register_modes():
    schema = _load_schema()
    documented = set(schema["definitions"]["function"]["properties"]["register"]["enum"])
    assert documented == catalog.REGISTER_MODES
