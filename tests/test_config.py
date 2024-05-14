from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Generator

import pytest
from packaging.version import Version
from pydantic import ValidationError

from invokeai.app.invocations.baseinvocation import BaseInvocation
from invokeai.app.services.config.config_common import AppConfigDict, ConfigMigration
from invokeai.app.services.config.config_default import (
    DefaultInvokeAIAppConfig,
    InvokeAIAppConfig,
)
from invokeai.app.services.config.config_migrate import ConfigMigrator, get_config, load_and_migrate_config
from invokeai.app.services.shared.graph import Graph
from invokeai.frontend.cli.arg_parser import InvokeAIArgs

invalid_v4_0_1_config = """
schema_version: 4.0.1

host: "192.168.1.1"
port: "ice cream"
"""

v4_config = """
schema_version: 4.0.0

precision: autocast
host: "192.168.1.1"
port: 8080
"""

invalid_v5_config = """
schema_version: 5.0.0

host: "192.168.1.1"
port: 8080
"""


v3_config = """
InvokeAI:
  Web Server:
    host: 192.168.1.1
    port: 8080
  Features:
    esrgan: true
    internet_available: true
    log_tokenization: false
    patchmatch: true
    ignore_missing_core_models: false
  Paths:
    outdir: /some/outputs/dir
    conf_path: /custom/models.yaml
  Model Cache:
    max_cache_size: 100
    max_vram_cache_size: 50
"""

v3_config_with_bad_values = """
InvokeAI:
  Web Server:
    port: "ice cream"
"""

invalid_config = """
i like turtles
"""


@pytest.fixture
def patch_rootdir(tmp_path: Path, monkeypatch: Any) -> None:
    """This may be overkill since the current tests don't need the root dir to exist"""
    monkeypatch.setenv("INVOKEAI_ROOT", str(tmp_path))


def test_config_migrator_registers_migrations() -> None:
    """Test that the config migrator registers migrations."""
    migrator = ConfigMigrator()

    def migration_func(config: AppConfigDict) -> AppConfigDict:
        return config

    migration_1 = ConfigMigration(from_version=Version("3.0.0"), to_version=Version("4.0.0"), function=migration_func)
    migration_2 = ConfigMigration(from_version=Version("4.0.0"), to_version=Version("5.0.0"), function=migration_func)

    migrator.register(migration_1)
    assert migrator._migrations == {migration_1}
    migrator.register(migration_2)
    assert migrator._migrations == {migration_1, migration_2}


def test_config_migrator_rejects_duplicate_migrations() -> None:
    """Test that the config migrator rejects duplicate migrations."""
    migrator = ConfigMigrator()

    def migration_func(config: AppConfigDict) -> AppConfigDict:
        return config

    migration_1 = ConfigMigration(from_version=Version("3.0.0"), to_version=Version("4.0.0"), function=migration_func)
    migrator.register(migration_1)

    # Re-register the same migration
    with pytest.raises(
        ValueError,
        match=f"A migration from {migration_1.from_version} or to {migration_1.to_version} has already been registered.",
    ):
        migrator.register(migration_1)

    # Register a migration with the same from_version
    migration_2 = ConfigMigration(from_version=Version("3.0.0"), to_version=Version("5.0.0"), function=migration_func)
    with pytest.raises(
        ValueError,
        match=f"A migration from {migration_2.from_version} or to {migration_2.to_version} has already been registered.",
    ):
        migrator.register(migration_2)

    # Register a migration with the same to_version
    migration_3 = ConfigMigration(from_version=Version("3.0.1"), to_version=Version("4.0.0"), function=migration_func)
    with pytest.raises(
        ValueError,
        match=f"A migration from {migration_3.from_version} or to {migration_3.to_version} has already been registered.",
    ):
        migrator.register(migration_3)


def test_config_migrator_contiguous_migrations() -> None:
    """Test that the config migrator requires contiguous migrations."""
    migrator = ConfigMigrator()

    def migration_1_func(config: AppConfigDict) -> AppConfigDict:
        return {"schema_version": "4.0.0"}

    def migration_3_func(config: AppConfigDict) -> AppConfigDict:
        return {"schema_version": "6.0.0"}

    migration_1 = ConfigMigration(from_version=Version("3.0.0"), to_version=Version("4.0.0"), function=migration_1_func)
    migration_3 = ConfigMigration(from_version=Version("5.0.0"), to_version=Version("6.0.0"), function=migration_3_func)

    migrator.register(migration_1)
    migrator.register(migration_3)
    with pytest.raises(ValueError, match="Migration functions are not continuous"):
        migrator._check_for_discontinuities(migrator._migrations)


def test_config_migrator_runs_migrations() -> None:
    """Test that the config migrator runs migrations."""
    migrator = ConfigMigrator()

    def migration_1_func(config: AppConfigDict) -> AppConfigDict:
        return {"schema_version": "4.0.0"}

    def migration_2_func(config: AppConfigDict) -> AppConfigDict:
        return {"schema_version": "5.0.0"}

    migration_1 = ConfigMigration(from_version=Version("3.0.0"), to_version=Version("4.0.0"), function=migration_1_func)
    migration_2 = ConfigMigration(from_version=Version("4.0.0"), to_version=Version("5.0.0"), function=migration_2_func)

    migrator.register(migration_1)
    migrator.register(migration_2)

    original_config = {"schema_version": "3.0.0"}
    migrated_config = migrator.run_migrations(original_config)
    assert migrated_config == {"schema_version": "5.0.0"}


def test_path_resolution_root_not_set(patch_rootdir: None):
    """Test path resolutions when the root is not explicitly set."""
    config = InvokeAIAppConfig()
    expected_root = InvokeAIAppConfig.find_root()
    assert config.root_path == expected_root


def test_read_config_from_file(tmp_path: Path, patch_rootdir: None):
    """Test reading configuration from a file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(v4_config)

    config = load_and_migrate_config(temp_config_file)
    assert config.host == "192.168.1.1"
    assert config.port == 8080


def test_migrate_v3_config_from_file(tmp_path: Path, patch_rootdir: None):
    """Test reading configuration from a file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(v3_config)

    config = load_and_migrate_config(temp_config_file)
    assert config.outputs_dir == Path("/some/outputs/dir")
    assert config.host == "192.168.1.1"
    assert config.port == 8080
    assert config.ram == 100
    assert config.vram == 50
    assert config.legacy_models_yaml_path == Path("/custom/models.yaml")
    # This should be stripped out
    assert not hasattr(config, "esrgan")


@pytest.mark.parametrize(
    "legacy_conf_dir,expected_value,expected_is_set",
    [
        # not set, expected value is the default value
        ("configs/stable-diffusion", Path("configs"), False),
        # not set, expected value is the default value
        ("configs\\stable-diffusion", Path("configs"), False),
        # set, best-effort resolution of the path
        ("partial_custom_path/stable-diffusion", Path("partial_custom_path"), True),
        # set, exact path
        ("full/custom/path", Path("full/custom/path"), True),
    ],
)
def test_migrate_v3_legacy_conf_dir_defaults(
    tmp_path: Path, patch_rootdir: None, legacy_conf_dir: str, expected_value: Path, expected_is_set: bool
):
    """Test reading configuration from a file."""
    config_content = f"InvokeAI:\n    Paths:\n        legacy_conf_dir: {legacy_conf_dir}"
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(config_content)

    config = load_and_migrate_config(temp_config_file)
    assert config.legacy_conf_dir == expected_value
    assert ("legacy_conf_dir" in config.model_fields_set) is expected_is_set


def test_migrate_v3_backup(tmp_path: Path, patch_rootdir: None):
    """Test the backup of the config file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(v3_config)

    load_and_migrate_config(temp_config_file)
    assert temp_config_file.with_suffix(".yaml.bak").exists()
    assert temp_config_file.with_suffix(".yaml.bak").read_text() == v3_config


def test_migrate_v4(tmp_path: Path, patch_rootdir: None):
    """Test migration from 4.0.0 to 4.0.1"""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(v4_config)

    conf = load_and_migrate_config(temp_config_file)
    assert Version(conf.schema_version) >= Version("4.0.1")
    assert conf.precision == "auto"  # we expect 'autocast' to be replaced with 'auto' during 4.0.1 migration


def test_failed_migrate_backup(tmp_path: Path, patch_rootdir: None):
    """Test the failed migration of the config file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(v3_config_with_bad_values)

    with pytest.raises(RuntimeError):
        load_and_migrate_config(temp_config_file)
    assert temp_config_file.with_suffix(".yaml.bak").exists()
    assert temp_config_file.with_suffix(".yaml.bak").read_text() == v3_config_with_bad_values
    assert temp_config_file.exists()
    assert temp_config_file.read_text() == v3_config_with_bad_values


def test_bails_on_invalid_config(tmp_path: Path, patch_rootdir: None):
    """Test reading configuration from a file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(invalid_config)

    with pytest.raises(AssertionError):
        load_and_migrate_config(temp_config_file)


@pytest.mark.parametrize("config_content", [invalid_v5_config, invalid_v4_0_1_config])
def test_bails_on_config_with_unsupported_version(tmp_path: Path, patch_rootdir: None, config_content: str):
    """Test reading configuration from a file."""
    temp_config_file = tmp_path / "temp_invokeai.yaml"
    temp_config_file.write_text(config_content)

    #    with pytest.raises(RuntimeError, match="Invalid schema version"):
    with pytest.raises(RuntimeError):
        load_and_migrate_config(temp_config_file)


def test_write_config_to_file(patch_rootdir: None):
    """Test writing configuration to a file, checking for correct output."""
    with TemporaryDirectory() as tmpdir:
        temp_config_path = Path(tmpdir) / "invokeai.yaml"
        config = InvokeAIAppConfig(host="192.168.1.1", port=8080)
        config.write_file(temp_config_path)
        # Load the file and check contents
        with open(temp_config_path, "r") as file:
            content = file.read()
            # This is a default value, so it should not be in the file
            assert "pil_compress_level" not in content
            assert "host: 192.168.1.1" in content
            assert "port: 8080" in content


def test_update_config_with_dict(patch_rootdir: None):
    """Test updating the config with a dictionary."""
    config = InvokeAIAppConfig()
    update_dict = {"host": "10.10.10.10", "port": 6060}
    config.update_config(update_dict)
    assert config.host == "10.10.10.10"
    assert config.port == 6060


def test_update_config_with_object(patch_rootdir: None):
    """Test updating the config with another config object."""
    config = InvokeAIAppConfig()
    new_config = InvokeAIAppConfig(host="10.10.10.10", port=6060)
    config.update_config(new_config)
    assert config.host == "10.10.10.10"
    assert config.port == 6060


def test_set_and_resolve_paths(patch_rootdir: None):
    """Test setting root and resolving paths based on it."""
    with TemporaryDirectory() as tmpdir:
        config = InvokeAIAppConfig()
        config._root = Path(tmpdir)
        assert config.models_path == Path(tmpdir).resolve() / "models"
        assert config.db_path == Path(tmpdir).resolve() / "databases" / "invokeai.db"


def test_singleton_behavior(patch_rootdir: None):
    """Test that get_config always returns the same instance."""
    get_config.cache_clear()
    config1 = get_config()
    config2 = get_config()
    assert config1 is config2
    get_config.cache_clear()


def test_default_config(patch_rootdir: None):
    """Test that the default config is as expected."""
    config = DefaultInvokeAIAppConfig()
    assert config.host == "127.0.0.1"


def test_env_vars(patch_rootdir: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Test that environment variables are merged into the config"""
    monkeypatch.setenv("INVOKEAI_ROOT", str(tmp_path))
    monkeypatch.setenv("INVOKEAI_HOST", "1.2.3.4")
    monkeypatch.setenv("INVOKEAI_PORT", "1234")
    config = InvokeAIAppConfig()
    assert config.host == "1.2.3.4"
    assert config.port == 1234
    assert config.root_path == tmp_path


def test_get_config_writing(patch_rootdir: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Test that get_config writes the appropriate files to disk"""
    # Trick the config into thinking it has already parsed args - this triggers the writing of the config file
    InvokeAIArgs.did_parse = True

    monkeypatch.setenv("INVOKEAI_ROOT", str(tmp_path))
    monkeypatch.setenv("INVOKEAI_HOST", "1.2.3.4")
    get_config.cache_clear()
    config = get_config()
    get_config.cache_clear()
    config_file_path = tmp_path / "invokeai.yaml"
    example_file_path = config_file_path.with_suffix(".example.yaml")
    assert config.config_file_path == config_file_path
    assert config_file_path.exists()
    assert example_file_path.exists()

    # The example file should have the default values
    example_file_content = example_file_path.read_text()
    assert "host: 127.0.0.1" in example_file_content
    assert "port: 9090" in example_file_content

    # It should also have the `remote_api_tokens` key
    assert "remote_api_tokens" in example_file_content

    # Neither env vars nor default values should be written to the config file
    config_file_content = config_file_path.read_text()
    assert "host" not in config_file_content

    # Undo our change to the singleton class
    InvokeAIArgs.did_parse = False


@contextmanager
def clear_config() -> Generator[None, None, None]:
    try:
        yield None
    finally:
        get_config.cache_clear()


@pytest.mark.xfail(
    reason="""
    Currently this test is failing due to an issue described in issue #5983.
"""
)
def test_deny_nodes():
    with clear_config():
        config = get_config()
        config.allow_nodes = ["integer", "string", "float"]
        config.deny_nodes = ["float"]

        # confirm graph validation fails when using denied node
        Graph(nodes={"1": {"id": "1", "type": "integer"}})
        Graph(nodes={"1": {"id": "1", "type": "string"}})

        with pytest.raises(ValidationError):
            Graph(nodes={"1": {"id": "1", "type": "float"}})

        # confirm invocations union will not have denied nodes
        all_invocations = BaseInvocation.get_invocations()

        has_integer = len([i for i in all_invocations if i.model_fields.get("type").default == "integer"]) == 1
        has_string = len([i for i in all_invocations if i.model_fields.get("type").default == "string"]) == 1
        has_float = len([i for i in all_invocations if i.model_fields.get("type").default == "float"]) == 1

        assert has_integer
        assert has_string
        assert not has_float
