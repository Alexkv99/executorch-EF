# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
This file is used to ensure that code changes in the compiler stack do not have
undesired effects on the final exported flatbuffer models. This is done by
creating these fixture tests where for each model located in the MODELS global
variable, we will export it to flatbuffer and output it in a json format. This
json format will then be compared against a "golden" set of json files which
exist in the fixtures/ directory.

If any changes to the flatbuffer format is desired, these json files can be
regenerated by running the following command:
```
buck run //executorch/exir/tests:generate_fixtures
```

To test for any changes to the flatbuffer format, the following command can be
run (this will also be run as a unittest for all diffs):
```
buck run //executorch/exir/tests:fixtures
```
"""

# pyre-strict

import unittest
from pathlib import Path
from typing import Any

import executorch.exir as exir
import executorch.exir.tests.models as models
import torch
from executorch.exir.serialize._program import _program_flatbuffer_to_json
from executorch.exir.tests.common import register_additional_test_aten_ops

from parameterized import parameterized


def get_module_path(module_name: str) -> Path:
    curr_dir = Path(__file__).resolve().parents[0]
    fixture_path = curr_dir / "fixtures"
    module_path = fixture_path / f"{module_name}.txt"
    return module_path


# pyre-ignore
def export_to_file(m: Any, inputs: Any) -> bytes:
    """
    Given a module and its inputs, return the json flatbuffer of that module.
    """
    exec_prog = (
        exir.capture(m, inputs, exir.CaptureConfig(pt2_mode=True))
        .to_edge(exir.EdgeCompileConfig(_check_ir_validity=False))
        .to_executorch()
    )
    flatbuffer = exec_prog.buffer
    output = _program_flatbuffer_to_json(flatbuffer)

    tag = "generated"
    heading = bytes(
        f"# @{tag} by //executorch/exir/tests:generate_fixtures\n\n",
        "utf-8",
    )
    return heading + output


def generate_json_fixtures() -> None:
    """
    Generates the json flatbuffers for all the models in MODELS and writes them
    to a file under fixtures/
    """
    for model_name, model in models.MODELS:
        # pyre-ignore
        output = export_to_file(model, model.get_random_inputs())

        assert isinstance(model_name, str)
        with open(get_module_path(model_name), "wb") as f:
            f.write(output)


if __name__ == "__main__":
    generate_json_fixtures()


class TestFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        register_additional_test_aten_ops()

    # pyre-ignore
    @parameterized.expand(models.MODELS)
    def test_fixtures_same(self, model_name: str, model: torch.nn.Module) -> None:
        """
        Checks that the generated json flatbuffers match the corresponding json
        flatbuffer in the fixtures/ folder.
        """
        output = export_to_file(model, model.get_random_inputs())

        with open(get_module_path(model_name), "rb") as f:
            expected_output = f.read()

        self.assertEqual(
            expected_output,
            output,
            "Please run `//executorch/exir/tests:generate_fixtures` to regenerate the fixtures.",
        )
