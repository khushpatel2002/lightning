# Copyright The Lightning AI team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import sys

import pytest
import torch
from lightning_utilities.core import module_available

from lightning.fabric.utilities.imports import _TORCH_GREATER_EQUAL_2_1
from lightning.pytorch import LightningModule, Trainer
from lightning.pytorch.demos.boring_classes import BoringModel
from lightning.pytorch.utilities.compile import from_compiled, to_uncompiled
from tests_pytorch.conftest import mock_cuda_count
from tests_pytorch.helpers.runif import RunIf


def skip_if_unsupported():
    if _TORCH_GREATER_EQUAL_2_1:
        from torch._dynamo.eval_frame import is_dynamo_supported

        if not is_dynamo_supported():
            pytest.skip("TorchDynamo unsupported")
    elif sys.platform == "win32" or sys.version_info >= (3, 11):
        pytest.skip("TorchDynamo unsupported")


@RunIf(min_torch="2.0.0")
@pytest.mark.skipif(sys.platform == "darwin", reason="https://github.com/pytorch/pytorch/issues/95708")
def test_trainer_compiled_model(tmp_path, monkeypatch):
    skip_if_unsupported()

    trainer_kwargs = {
        "default_root_dir": tmp_path,
        "fast_dev_run": True,
        "logger": False,
        "enable_checkpointing": False,
        "enable_model_summary": False,
        "enable_progress_bar": False,
    }

    model = BoringModel()
    compiled_model = torch.compile(model)
    assert model._compiler_ctx is compiled_model._compiler_ctx  # shared reference

    # can train with compiled model
    trainer = Trainer(**trainer_kwargs)
    trainer.fit(compiled_model)
    assert trainer.model._compiler_ctx["compiler"] == "dynamo"

    # the compiled model can be uncompiled
    to_uncompiled_model = to_uncompiled(compiled_model)
    assert model._compiler_ctx is None
    assert compiled_model._compiler_ctx is None
    assert to_uncompiled_model._compiler_ctx is None

    # the compiled model needs to be passed
    with pytest.raises(ValueError, match="required to be a compiled LightningModule"):
        to_uncompiled(to_uncompiled_model)

    # the uncompiled model can be fitted
    trainer = Trainer(**trainer_kwargs)
    trainer.fit(model)
    assert trainer.model._compiler_ctx is None

    # some strategies do not support it
    if module_available("deepspeed"):
        compiled_model = torch.compile(model)
        mock_cuda_count(monkeypatch, 2)
        trainer = Trainer(strategy="deepspeed", accelerator="cuda", **trainer_kwargs)
        with pytest.raises(RuntimeError, match="Using a compiled model is incompatible with the current strategy.*"):
            trainer.fit(compiled_model)

    # ddp does
    trainer = Trainer(strategy="ddp", **trainer_kwargs)
    trainer.fit(compiled_model)

    # an exception is raised
    trainer = Trainer(**trainer_kwargs)
    with pytest.raises(TypeError, match="must be a `Light"):
        trainer.fit(object())


@RunIf(min_torch="2.0.0")
def test_compile_uncompile():
    skip_if_unsupported()

    model = BoringModel()
    compiled_model = torch.compile(model)

    def has_dynamo(fn):
        return any(el for el in dir(fn) if el.startswith("_torchdynamo"))

    from_compiled_model = from_compiled(compiled_model)
    assert isinstance(from_compiled_model, LightningModule)
    assert from_compiled_model._compiler_ctx is not None
    assert has_dynamo(from_compiled_model.forward)
    assert has_dynamo(from_compiled_model.training_step)
    assert has_dynamo(from_compiled_model.validation_step)
    assert has_dynamo(from_compiled_model.test_step)
    assert has_dynamo(from_compiled_model.predict_step)

    to_uncompiled_model = to_uncompiled(model)
    assert to_uncompiled_model._compiler_ctx is None
    assert to_uncompiled_model.forward == model.forward
    assert to_uncompiled_model.training_step == model.training_step
    assert to_uncompiled_model.validation_step == model.validation_step
    assert to_uncompiled_model.test_step == model.test_step
    assert to_uncompiled_model.predict_step == model.predict_step
    assert not has_dynamo(to_uncompiled_model.forward)
    assert not has_dynamo(to_uncompiled_model.training_step)
    assert not has_dynamo(to_uncompiled_model.validation_step)
    assert not has_dynamo(to_uncompiled_model.test_step)
    assert not has_dynamo(to_uncompiled_model.predict_step)


@pytest.mark.skipif(sys.platform == "darwin", reason="https://github.com/pytorch/pytorch/issues/95708")
@RunIf(min_torch="2.0.0")
def test_trainer_compiled_model_that_logs(tmp_path):
    skip_if_unsupported()

    class MyModel(BoringModel):
        def training_step(self, batch, batch_idx):
            loss = self.step(batch)
            self.log("loss", loss)
            return loss

    model = MyModel()
    compiled_model = torch.compile(model)

    trainer = Trainer(
        default_root_dir=tmp_path,
        fast_dev_run=True,
        enable_checkpointing=False,
        enable_model_summary=False,
        enable_progress_bar=False,
    )
    trainer.fit(compiled_model)

    assert set(trainer.callback_metrics) == {"loss"}
