"""Pure tests for explicit POC workflow selection."""

import pytest

from agents.workflow_agent import WORKFLOW_DEFINITIONS, detect_workflow


@pytest.mark.parametrize("mode", ["sequential", "parallel", "supervisor", "loop", "cycle"])
def test_detects_each_explicit_workflow(mode):
    assert detect_workflow(f"workflow: {mode} prepare a legal notice") == mode


def test_workflow_definitions_are_bounded_and_described():
    assert set(WORKFLOW_DEFINITIONS) == {"sequential", "parallel", "supervisor", "loop", "cycle"}
    for workflow in WORKFLOW_DEFINITIONS.values():
        assert workflow["agents"]
        assert workflow["pattern"]
