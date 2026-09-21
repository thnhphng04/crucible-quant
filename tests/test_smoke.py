import importlib
import pkgutil

import quantcrucible


def test_every_subpackage_imports() -> None:
    names = [m.name for m in pkgutil.walk_packages(quantcrucible.__path__, "quantcrucible.")]
    assert "quantcrucible.core.strategy" in names
    for name in names:
        importlib.import_module(name)
