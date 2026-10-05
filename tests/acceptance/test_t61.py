"""Protocol checks for the v3 computation diagnostic; no paid calls."""
import json
from pathlib import Path

import pytest

from scripts.planning.gdpevo_tool_diagnostic import CONTROL, PUBLIC, TRAINING, _messages, _parse
from scripts.planning.gdpevo_expansion import actor_view


def datasets():
    return (json.loads(PUBLIC.read_text()), json.loads(TRAINING.read_text()),
            json.loads(CONTROL.read_text()))


@pytest.mark.parametrize('task_id', ('packages-train-04', 'kits-train-03'))
def test_text_and_compute_share_public_task_view(task_id):
    public, training, _ = datasets()
    task = next(t for t in public['tasks'] if t['task_id'] == task_id)
    view = actor_view(public, training, task_id, 'train')
    text = _messages(public, training, task, compute=False)
    compute = _messages(public, training, task, compute=True)
    assert json.loads(text[1]['content'])['task'] == view['task']
    assert json.loads(compute[1]['content'])['task'] == view['task']
    assert text[0]['content'].replace('\n', '') in compute[0]['content'].replace('\n', '')
    assert 'computation_execute' not in text[0]['content']
    assert 'computation_execute' in compute[0]['content']
    for message in (text + compute):
        assert all(token not in message['content'] for token in ('rubrics', 'expected', 'gold', 'manual_checks'))


@pytest.mark.parametrize('payload', ('[]', 'not-json', 'null'))
def test_parse_rejects_non_object_decisions(payload):
    with pytest.raises(ValueError):
        _parse(payload)


def test_compute_prompt_requires_explicit_persistent_load_and_agent_code():
    public, training, _ = datasets()
    task = next(t for t in public['tasks'] if t['task_id'] == 'packages-train-04')
    system = _messages(public, training, task, compute=True)[0]['content']
    assert 'read_names=["task"]' in system
    assert '自己编写Python' in system
    assert '固定planner' in system


def test_taskset_and_control_are_frozen_inputs_not_runtime_answers():
    public, training, control = datasets()
    assert len(public['tasks']) == 40
    assert set(training['tasks']) == {t['task_id'] for t in public['tasks'] if t['split'] == 'train'}
    assert set(control['rubrics']) == {t['task_id'] for t in public['tasks']}
    assert 'answers' not in json.dumps(public, ensure_ascii=False)


def test_output_directory_is_external_and_script_has_no_host_exec_fallback():
    source = Path('scripts/planning/gdpevo_tool_diagnostic.py').read_text()
    assert 'attempt-v3-tool-diagnostic-20261005' in source
    assert 'subprocess.run' not in source
    assert 'create_deep_agent' in source and 'ComputationService' in source
