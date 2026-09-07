"""M0 data and real script runs, isolated from the saved project outputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Circle

import config
from scripts import compare_layouts, generate_baselines, search_variable_n


@pytest.fixture(scope="session")
def reference():
    path = Path(__file__).parent / "fixtures" / "reference.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def reference_cases(reference):
    return {case["name"]: case for case in reference["cases"]}


def _run_script(module, root):
    """Run main with real CLI defaults, exporters, plotting and directory setup.

    Only output roots/argv are redirected. Spies observe figures at save time
    and search results, while still calling the original functions.
    """
    result = {"root": root, "figures": {}}
    original_savefig = Figure.savefig

    def capture_figure(fig, fname, *args, **kwargs):
        result["figures"][Path(fname).relative_to(root).as_posix()] = [
            {
                "labels": [(text.get_text(), text.get_position()) for text in ax.texts],
                "circles": [
                    (tuple(patch.center), patch.radius)
                    for patch in ax.patches
                    if isinstance(patch, Circle)
                ],
            }
            for ax in fig.axes
        ]
        return original_savefig(fig, fname, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        for name, directory in (
            ("COORDINATES_DIR", "coordinates"),
            ("FIGURES_DIR", "figures"),
            ("SUMMARIES_DIR", "summaries"),
        ):
            patch.setattr(config, name, root / directory)
            patch.setattr(module, name, root / directory)
        patch.setattr(sys, "argv", [module.__file__])
        patch.setattr(Figure, "savefig", capture_figure)

        if module is search_variable_n:
            original_search = module.search_variable_n

            def capture_search(n_min, n_max, geometry_config):
                result["search_inputs"] = (n_min, n_max, geometry_config)
                found = original_search(n_min, n_max, geometry_config)
                # Stop before exporting if the audited high-level result changes.
                assert (len(found[0]), len(found[1]), len(found[2])) == (415, 415, 18), (
                    "M0 STOP: default search differs from audited 415 / 18; "
                    "investigate without changing production code to fit the counts."
                )
                result["search"] = found
                return found

            patch.setattr(module, "search_variable_n", capture_search)

        module.main()
    return result


@pytest.fixture(scope="session")
def baseline_run(tmp_path_factory):
    return _run_script(generate_baselines, tmp_path_factory.mktemp("m0_baselines"))


@pytest.fixture(scope="session")
def comparison_run(tmp_path_factory):
    return _run_script(compare_layouts, tmp_path_factory.mktemp("m0_comparison"))


@pytest.fixture(scope="session")
def search_run(tmp_path_factory):
    return _run_script(search_variable_n, tmp_path_factory.mktemp("m0_search"))
