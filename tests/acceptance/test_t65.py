import json
import pytest
from scripts.planning.gdpevo_dense_fewshot import examples_for
from scripts.planning.gdpevo_v4_training import load_inputs
from scripts.planning.gdpevo_v5_compute_training import dense_example_suffix, run

@pytest.fixture(scope='module')
def inputs():
    return load_inputs()

@pytest.mark.parametrize('group', ['quotes', 'packages', 'kits', 'revisions'])
def test_dense_train_examples_group_and_self_exclusion(inputs, group):
    public, training, control, answers, train, test = inputs
    task = next(t for t in train if t['group_id'] == group)
    examples = examples_for(task, train, answers, exclude_self=True)
    assert len(examples) == 4
    assert task['task_id'] not in {e['task_id'] for e in examples}
    assert all(e['task_id'].startswith(group + '-train-') and e['input'] for e in examples)

@pytest.mark.parametrize('group', ['quotes', 'packages', 'kits', 'revisions'])
def test_test_examples_contain_all_five_train_inputs_only(inputs, group):
    public, training, control, answers, train, test = inputs
    task = next(t for t in test if t['group_id'] == group)
    suffix = dense_example_suffix(task, train, answers)
    assert '-test-' not in suffix
    examples = examples_for(task, train, answers, exclude_self=False)
    assert len(examples) == 5
    for e in examples:
        original = next(t for t in train if t['task_id'] == e['task_id'])
        assert e['input'] == original['input']
        assert e['correct_decision'] == answers['tasks'][e['task_id']][0]
