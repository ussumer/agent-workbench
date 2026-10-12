"""Separate trusted train-only feedback control. Disabled for all test solvers."""
import argparse
import hmac
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class JudgeError(ValueError):
    pass

class TrainJudge:
    def __init__(self, group, enabled=False):
        self.group = Path(group).resolve()
        self.enabled = enabled

    def evaluate(self, task_id, prediction):
        if not self.enabled:
            raise JudgeError('judge disabled')
        if not isinstance(task_id, str) or not re.fullmatch(r'train_00[1-5]', task_id):
            raise JudgeError('train task required')
        if not isinstance(prediction, dict):
            raise JudgeError('object prediction required')
        import yaml
        index = yaml.safe_load((self.group / 'task_group.yaml').read_text(encoding='utf-8'))
        task = next((x for x in index['train_tasks'] if x['task_id'] == task_id), None)
        if task is None:
            raise JudgeError('train task not registered')
        relative = Path(task['eval']['script'])
        script = (self.group / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not script.is_relative_to(self.group) or not script.is_file():
            raise JudgeError('evaluator registration invalid')
        with tempfile.TemporaryDirectory(prefix='t67-train-feedback-') as temporary:
            path = Path(temporary) / 'submission.json'
            path.write_text(json.dumps(prediction), encoding='utf-8')
            result = subprocess.run(['bash', str(script), str(path)], cwd=script.parent,
                                    capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise JudgeError('submission could not be scored')
        try:
            score = json.loads(result.stdout)['score']
        except (ValueError, KeyError, TypeError) as error:
            raise JudgeError('evaluator result invalid') from error
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise JudgeError('evaluator result invalid')
        return {'score': score, 'correct': abs(score - 1) < 1e-9, 'train_only': True}

def handler_for(judge, token):
    if not token:
        raise JudgeError('operator token required')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'service':'train-feedback-control','judge_enabled':judge.enabled})
            else:
                self.reply(404, {'error':'not_found'})
        def do_POST(self):
            if self.path != '/api/judge' or not judge.enabled:
                return self.reply(404, {'error':'not_found'})
            if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                return self.reply(401, {'error':'unauthorized'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 1048576:
                    return self.reply(413, {'error':'invalid_payload_size'})
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict) or set(value) != {'task_id','prediction'}:
                    raise JudgeError('invalid fields')
                feedback = judge.evaluate(value['task_id'],value['prediction'])
            except (ValueError, OSError, subprocess.SubprocessError):
                return self.reply(400, {'error':'invalid_train_submission'})
            self.reply(200,feedback)
    return Handler

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--group',type=Path,required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(TrainJudge(args.group,os.environ.get('TASK_ENV_ENABLE_JUDGE')=='1'),os.environ.get('TASK_ENV_JUDGE_TOKEN','')))
    server.serve_forever()
