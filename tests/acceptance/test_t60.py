"""Assert complete Curator evidence against paid train attempts, without new calls."""
import json
from copy import deepcopy

import pytest

from scripts.planning.gdpevo_curator_prepare import ORIGINAL, prepare, read_paid_request
from scripts.planning.gdpevo_v3_closed_loop import build_curator_records
from scripts.planning.gdpevo_v3_scoped import BASE, GROUPS, SESSION, scoped_curator_input


@pytest.fixture
def learning():
    return (json.loads((BASE / 'frozen/gdpevo-procurement-v3-training.json').read_text()),
            json.loads((BASE / 'training-records.json').read_text()))


@pytest.mark.parametrize('group', GROUPS)
def test_full_task_and_feedback_survive_group_compaction(learning, group):
    training, records = learning
    material = scoped_curator_input(training, records, group)
    source = [r for r in records if r['task']['group_id'] == group]
    assert material['schema_version'] == 2
    for old, row in zip(source, material['train_records'], strict=True):
        assert row['actor_task'] == old['task']
        assert len(row['attempts']) == len(old['attempts'])
        for original, attempt in zip(old['attempts'], row['attempts'], strict=True):
            assert attempt['decision'] == original['model_output']
            assert attempt['grade'] == original['grade']
            assert attempt['feedback'] == original['feedback']
            assert 'diagnostics' in attempt['feedback']
            assert 'arithmetic_audit' in attempt['feedback']
    assert not any(r['task']['task_id'] in json.dumps(material) for r in records
                   if r['task']['group_id'] != group)


def test_old_scoped_inputs_still_replay_exactly(learning):
    training, records = learning
    for group in GROUPS:
        old = json.loads((SESSION / f'curator-input-{group}.json').read_text())
        assert scoped_curator_input(training, records, group, version=1) == old


@pytest.mark.parametrize('mutation', ('test', 'duplicate', 'missing_input', 'missing_request'))
def test_incomplete_or_wrong_stage_train_is_rejected(learning, mutation):
    training, records = learning
    records = deepcopy(records)
    if mutation == 'test':
        records[0]['task']['split'] = 'test'
    elif mutation == 'duplicate':
        records.append(deepcopy(records[0]))
    else:
        del records[0]['task'][mutation.removeprefix('missing_')]
    with pytest.raises(ValueError):
        build_curator_records(training, records)


def test_curator_changes_do_not_mutate_training_evidence(learning):
    training, records = learning
    before = deepcopy((training, records))
    result = scoped_curator_input(training, records, 'quotes')
    result['train_records'][0]['actor_task']['input']['budget_cents'] = 0
    result['train_records'][0]['attempts'][0]['feedback']['diagnostics'].append({'code': 'changed'})
    result['policies'][0]['fields'].append('changed')
    assert (training, records) == before


def test_prepare_replays_actual_requests_without_models_or_private_answers(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts.planning.gdpevo_refinement import RealCalls

    original_read = Path.read_text
    def only_public_read(path, *args, **kwargs):
        if 'private' in path.parts or path.name.endswith(('-control.json', '-answers.json')):
            raise AssertionError('private data read: ' + str(path))
        return original_read(path, *args, **kwargs)
    def no_call(*args, **kwargs):
        raise AssertionError('new model call during preparation')
    monkeypatch.setattr(Path, 'read_text', only_public_read)
    monkeypatch.setattr(RealCalls, 'call', no_call)
    output = tmp_path / 'complete-curator'
    report = prepare(output)
    assert report['model_calls'] == 0 and report['production_assignment_changed'] is False
    assert set(report['groups']) == set(GROUPS)
    assert sum(g['attempt_count'] for g in report['groups'].values()) == 35
    for group in GROUPS:
        request = json.loads((output / f'{group}-request.json').read_text())
        material = json.loads(request['messages'][1]['content'])
        assert '不调用工具' in material['actor_visible_common']['system']
        assert material['actor_visible_common']['environment']['public_contract']
        assert len(material['train_records']) == 5
        assert all(r['actor_task']['split'] == 'train' for r in material['train_records'])
        assert len(json.dumps(request, ensure_ascii=False).encode()) == report['groups'][group]['request_bytes']
        assert report['groups'][group]['request_bytes'] < 131072
    with pytest.raises(FileExistsError):
        prepare(output)


def test_bad_input_version_is_not_silently_downgraded(learning):
    training, records = learning
    with pytest.raises(ValueError, match='version'):
        scoped_curator_input(training, records, 'quotes', version=3)


def test_original_request_tampering_cannot_be_accepted_as_actor_view(tmp_path):
    source = ORIGINAL / 'train/quotes-train-01'
    request = json.loads((source / 'request.json').read_text())
    view = json.loads(request['messages'][1]['content'])
    view['task']['input']['budget_cents'] += 1
    request['messages'][1]['content'] = json.dumps(view, ensure_ascii=False)
    (tmp_path / 'request.json').write_text(json.dumps(request))
    (tmp_path / 'call-result.json').write_bytes((source / 'call-result.json').read_bytes())
    with pytest.raises(ValueError, match='paid wire record'):
        read_paid_request(tmp_path)
