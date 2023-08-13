"""
Test the refactored model config classes.
"""

import pytest

from hashlib import sha256
from invokeai.app.services.config import InvokeAIAppConfig
from invokeai.backend.model_management2.storage.sql import (
    ModelConfigStoreSQL,
    ModelConfigStore,
    UnknownModelException,
)
from invokeai.backend.model_management2.model_config import (
    TextualInversionConfig,
    DiffusersConfig,
    VaeDiffusersConfig,
)


@pytest.fixture
def store(datadir) -> ModelConfigStore:
    InvokeAIAppConfig.get_config(root=datadir)
    return ModelConfigStoreSQL(datadir / "configs" / "models.db")


def example_config() -> TextualInversionConfig:
    return TextualInversionConfig(
        path="/tmp/pokemon.bin",
        name="old name",
        base_model="sd-1",
        model_type="embedding",
        model_format="embedding_file",
        author="Anonymous",
    )


def test_add(store: ModelConfigStore):
    raw = dict(
        path="/tmp/foo.ckpt",
        name="model1",
        base_model="sd-1",
        model_type="main",
        config="/tmp/foo.yaml",
        model_variant="normal",
        model_format="checkpoint",
    )
    store.add_model("key1", raw)
    config1 = store.get_model("key1")
    assert config1 is not None
    raw["name"] = "model2"
    raw["base_model"] = "sd-2"
    raw["model_format"] = "diffusers"
    raw.pop("config")
    store.add_model("key2", raw)
    config2 = store.get_model("key2")
    assert config1.name == "model1"
    assert config2.name == "model2"
    assert config1.base_model == "sd-1"
    assert config2.base_model == "sd-2"


def test_update(store: ModelConfigStore):
    config = example_config()
    store.add_model("key1", config)
    config = store.get_model("key1")
    assert config.name == "old name"

    config.name = "new name"
    store.update_model("key1", config)
    new_config = store.get_model("key1")
    assert new_config.name == "new name"

    try:
        store.update_model("unknown_key", config)
        assert False, "expected UnknownModelException"
    except UnknownModelException:
        assert True


def test_delete(store: ModelConfigStore):
    config = example_config()
    store.add_model("key1", config)
    config = store.get_model("key1")
    store.del_model("key1")
    try:
        config = store.get_model("key1")
        assert False, "expected fetch of deleted model to raise exception"
    except UnknownModelException:
        assert True

    try:
        store.del_model("unknown")
        assert False, "expected delete of unknown model to raise exception"
    except UnknownModelException:
        assert True


def test_exists(store: ModelConfigStore):
    config = example_config()
    store.add_model("key1", config)
    assert store.exists("key1")
    assert not store.exists("key2")


def test_filter(store: ModelConfigStore):
    config1 = DiffusersConfig(
        path="/tmp/config1", name="config1", base_model="sd-1", model_type="main", tags=["sfw", "commercial", "fantasy"]
    )
    config2 = DiffusersConfig(
        path="/tmp/config2", name="config2", base_model="sd-1", model_type="main", tags=["sfw", "commercial"]
    )
    config3 = VaeDiffusersConfig(path="/tmp/config3", name="config3", base_model="sd-1", model_type="vae", tags=["sfw"])
    for c in config1, config2, config3:
        store.add_model(sha256(c.name.encode("utf-8")).hexdigest(), c)
    matches = store.search_by_type(model_type="main")
    assert len(matches) == 2
    assert matches[0].name in {"config1", "config2"}

    matches = store.search_by_type(model_type="vae")
    assert len(matches) == 1
    assert matches[0].name == "config3"
    assert matches[0].hash == sha256("config3".encode("utf-8")).hexdigest()

    matches = store.search_by_tag(["sfw"])
    assert len(matches) == 3

    matches = store.search_by_tag(["sfw", "commercial"])
    assert len(matches) == 2

    matches = store.search_by_tag(["sfw", "commercial", "fantasy"])
    assert len(matches) == 1

    matches = store.search_by_tag(["sfw", "commercial", "fantasy", "nonsense"])
    assert len(matches) == 0

    matches = store.all_models()
    assert len(matches) == 3
