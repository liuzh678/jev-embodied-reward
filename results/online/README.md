# Online training results

Final results are intentionally absent until the source run finishes. A missing or
false `summary.json` completion marker, an API failure or quota stop, or a trainer
that exits early must not be presented as a completed experiment.

Use Python 3.10 or newer and install the plotting dependency:

```bash
python -m pip install -r requirements-results.txt
python scripts/export_training_results.py --run-dir /path/to/completed/run
```

The repository containing the script is the default destination. Use
`--repo-dir /path/to/repository` to choose another destination. To wait for the
current run, start the following command explicitly in the foreground:

```bash
python scripts/export_training_results.py --run-dir /path/to/training/run --watch
```

The watcher checks every 45 seconds, reads local files only, and never starts,
restarts, or modifies training. It stops without exporting when the trainer exits
without completion, including an active API failure or quota stop. While the
trainer is alive, stop markers from an earlier attempt do not end the watcher.
An older stop marker is also ignored after recorded training steps have advanced
beyond its stop step.
The supervisor state `waiting_after_transient_api_failure` also keeps the
watcher polling during an automatic transport-error recovery. A stopped run in
`requires_manual_review` does not qualify as a completed result.
Trainer detection uses `auto_resume_status.json` and, on Linux, `/proc`; supply
`--watch-pid TRAINER_PID` when automatic detection is unavailable. The watcher
does not upload files or update GitHub. Stop it with Ctrl+C.

Before writing, the exporter requires the literal JSON boolean
`summary.completed: true`, matching `summary.steps` and `config.total_steps`, and
a final evaluation at that same step. All evaluations must contain 10 episodes,
and their success counts must agree with their reported rates. It also validates
episode ordering, lengths, and the sum of process and success returns. The last
metrics step must equal the target: the trainer scores and records its final
episode even when it is shorter than the normal horizon. Missing final metrics
are rejected, even if the completion marker is present.

The completed export creates:

- `training_episodes.csv`: numeric fields `step`, `episode`, `episode_length`,
  `process_return`, `success_return`, `episode_return`, and `native_success`.
- `training_bins_10k.csv`: step-weighted reward statistics in 10,000-step bins.
- `evaluation.csv` and `evaluation.json`: numeric fields `step`, `episodes`,
  `successes`, `success_rate`, and `mean_length`.
- `final_summary.json`: the completion status, coverage, aggregate rewards,
  final evaluation, and hashes of the source files and generated artifacts.
- `../../assets/figures/training_final.png`: three panels showing process reward
  per step, total reward per step, and evaluation success rate.

Reward bins are right-closed `(0, 10000]`, `(10000, 20000]`, and so on. Each whole
episode is assigned by its recorded end step. Within a bin, process reward per
step is `sum(process_return) / sum(episode_length)`; total reward per step is
`sum(episode_return) / sum(episode_length)`. This weights episodes by their actual
length. It does not average episode means or infer transition rewards inside an
episode. Only recorded episodes contribute; gaps from discarded episodes are
excluded. The final recorded episode must reach the completed target step.

These are results from one training seed. Evaluation uses 10 trials per
checkpoint; these trials are not independent training seeds. The figure contains
no multi-seed confidence intervals or uncertainty bands. Repeated evaluations
at the same step during resume are retained as recorded.

Only explicitly allowed numeric result fields are exported. API credentials,
token counts, raw API caches, paths, trial metadata, and arbitrary source JSON
fields are not copied. Source configuration and metadata are represented only
by SHA-256 hashes. The source run is always read-only, and the root README is not
modified. Exit code `0` means a final export succeeded, `2` means incomplete, and
`1` means validation or writing failed.
