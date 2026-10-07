# Dense train-only few-shot ablation (2026-10-07)

This is a new experiment after T64. It does not change the production assignment or T64
evidence. The Actor is the text/no-tools v3 decision Actor used by the existing training
runner, not the T64 DeepAgents/OpenSandbox compute Actor.

The first attempt, `attempt-dense-fewshot-20261007`, is retained as a protocol failure: its
example helper did not enforce same-group examples and omitted the example input. It produced
6/60 business-successful held-out rows with mean score 0.7872, but it is not a valid dense
few-shot comparison.

The corrected attempt is
`/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261007/attempt-dense-fewshot-v2-20261007`.
It injects all five correct examples from the same train group, including public input and
correct decision. Train validation excludes the current task; held-out test receives train
examples only and no test feedback. There are 20 train validation rows and 20 held-out tasks
with three repeats (80 model calls, 664,151 input and 46,583 output tokens).

| split | scored | business success | mean score |
|---|---:|---:|---:|
| train validation | 20/20 | 8/20 | 0.861538 |
| held-out repeat 1 | 20/20 | 5/20 | 0.826923 |
| held-out repeat 2 | 20/20 | 5/20 | 0.826923 |
| held-out repeat 3 | 20/20 | 5/20 | 0.826923 |

The corrected dense examples improve over the invalid first attempt, but this is not a direct
comparison with T64's compute Actor. It also does not prove learning gain: the test set was
already used in prior development work, and no independent sealed test was introduced here.
The result supports the diagnosis that the original few-shot arm was under-specified, but the
remaining 15/20 held-out failures per repeat show that examples alone do not solve the
procurement, source-selection, freight, and kit interactions.
