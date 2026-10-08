# Offline reward diagnostics

The figures in this release evaluate reward signals on a fixed pair of LIBERO rollouts. They do not report reinforcement-learning policy success rates. Both experiments use LIBERO Spatial task 5:

> Pick up the black bowl on the ramekin and place it on the plate.

An independent successful rollout provides reference inputs where a method needs a goal. A different successful rollout and one failed rollout provide the plotted test pair. Success/failure labels are used to select and display the rollouts, and are excluded from the reward-model requests. One pair cannot establish accuracy, statistical significance, or performance across LIBERO.

## Adapted progress comparison — September 23, 2026

![Adapted reward diagnostics](../assets/figures/offline_baseline_comparison.png)

This figure compares locally adapted progress signals. For the five plotted methods, the cumulative curve is built from positive improvements beyond the previous best progress or similarity estimate. Both rollout curves are divided by that method's successful-rollout final cumulative reward. Thus, every successful curve ends at 1 by construction. The dashed ideal-outcome reference is a visual aid; it is neither a dense ground-truth annotation nor an input to the models.

The local adaptations and sampling counts are part of this diagnostic protocol. They should not be described as identical to every model's native reward definition.

| Method | Signal before the local adaptation | Success / failure samples | Failure / success final return |
| --- | --- | ---: | ---: |
| VIP | Negative distance to the independent goal embedding | 183 / 261 | 0.2457 |
| RoboMeter-4B LIBERO, INT8 | Predicted absolute progress | 8 / 8 | 0.6293 |
| RoboDopamine GRM-3B, NF4 | Fused progress from incremental, forward, and backward modes | 8 / 8 | 0.3522 |
| JEV-1.13, simulator state | Expected absolute progress over eleven bins | 16 / 16 | 0.4326 |
| RoboCLIP-compatible S3D prefix diagnostic | Prefix-to-reference video similarity | 17 / 17 | 0.9783 |
| TemporalOT, raw-output figure only | Native negative transport cost | 88 / 88 | 2.5297, a cost-magnitude ratio |

The JEV curve uses the probability-weighted expectation over 0%, 10%, ..., 100% absolute-progress bins. Its reward is the positive increment of the running-best expectation. These probabilities are not calibrated accuracy estimates.

RoboCLIP is represented by an explicitly qualified S3D prefix diagnostic, using the official DeepMind MIL-NCE encoder. The original project's older checkpoint link was unavailable when this experiment was collected. This is not an exact reproduction of RoboCLIP's original reward setup.

TemporalOT preserves its native negative costs. The failed rollout is more negative than the successful rollout. Its ratio of 2.5297 does not mean it rewards failure 2.53 times more. TemporalOT is excluded from the main comparison and retained in the raw-output figure and data package.

## Native reward protocol — September 24, 2026

![Native reward protocol diagnostics](../assets/figures/native_protocol_comparison.png)

The second experiment aligns VIP, RoboMeter, RoboDopamine, and JEV to the same sixteen evaluation locations on the same rollout pair. Exact control steps are provided in [protocol_manifest.json](../results/offline/protocol_manifest.json). The first point is the reset observation and is not counted as an environment transition.

| Method | Reward accumulated by this protocol | Failure / success final return |
| --- | --- | ---: |
| VIP | Native negative Euclidean distance to the independent goal embedding | 1.0985 |
| RoboMeter | Absolute predicted progress, following its default LIBERO wrapper behavior | 0.3412 |
| RoboDopamine | Mean of the incremental, forward, and backward hop/reward outputs | 0.1598 |
| JEV | Locally declared positive increase beyond the previous best expected absolute progress | 0.4438 |

JEV's adapter is a local contract; it is not a native reward head supplied by JEV. VIP's native distance reward has a negative denominator in the final-return normalization. Its ratio of 1.0985 reflects more negative accumulated distance on the failed rollout, and must not be read as a cross-model positive-reward ranking.

This experiment follows the publicly described RARM Figure 5 comparison protocol where the available implementations allow it. It is a reproducible subset on a LIBERO task, rather than a numerical reproduction of the paper's real-world bimanual folding result. At collection time, the original RARM model/experiment implementation and Figure 5 baseline adaptation details were not available in the local source materials; GVL also required a separate proprietary service. No RARM or GVL result is included here.

## Input modalities and model configurations

JEV receives whitelisted structured simulator state: end-effector and object poses, gripper width, contact information, and geometry relative to the goal. Native success flags, environment rewards, `done`, future states, measured oracle flags, waypoints, controller confidence, and the episode result are removed. The independent reference start/goal states use the same exclusions.

The principal visual baselines receive RGB observations. Consequently, the figures compare diagnostic signal behavior under different input modalities; they do not establish a fair visual-model ranking. JEV is marked as simulator-state input in the plots. RoboMeter uses INT8 and RoboDopamine uses NF4 4-bit quantization, so their results should not be represented as full-precision paper replications.

## Raw outputs

![Raw model outputs](../assets/figures/raw_model_outputs.png)

This figure preserves separate signal axes for each method. It includes TemporalOT and makes the difference between raw model output and a cumulative reward adapter visible. The PNGs and accompanying PDFs are original experiment exports, copied without smoothing or changing the reported curves.

## Difference from online reinforcement learning

The offline JEV adapter above estimates absolute progress and accumulates improvements beyond the previous best estimate. The current online adapter instead classifies each observed transition as `progress`, `neutral`, or `regression`, assigning `+1`, `0`, or `-1`. It additionally gives a one-time `+200` bonus when the simulator reports task success. The ungated online configuration has no confidence threshold.

Online training and evaluation start from official LIBERO task initial states. The diagnostic trajectories and absolute-progress bins shown here are a separate experiment. An offline curve cannot demonstrate that either adapter improves policy learning. Final online learning curves, independent evaluation results, and checkpoint artifacts belong to a separate release after training completes.

## Data files and reproduction of the plotted arrays

The [comparison.csv](../results/offline/comparison.csv) table contains the adapted experiment's final returns. The [summary.json](../results/offline/summary.json) file includes each adapter definition and its portable data path. The native experiment is summarized separately in [native_protocol_summary.json](../results/offline/native_protocol_summary.json).

Original numeric arrays are bundled under `results/offline/adapted/` and `results/offline/native/`. Array names and values are preserved. The consolidated `native_protocol_curves.npz` contains the strict protocol's instantaneous and cumulative arrays for all four methods.

[curves_long.csv](../results/offline/curves_long.csv) exports 846 samples from both protocols, including TemporalOT. Its columns are:

| Column | Meaning |
| --- | --- |
| `protocol` | `adapted_progress_diagnostic` or `native_reward_protocol` |
| `method`, `role` | Method identifier and successful/failed rollout |
| `sample_index` | Zero-based index within that method's plotted samples |
| `normalized_frame_index` | Plot coordinate `linspace(0, 1, sample_count)`; it is not elapsed time |
| `source_index`, `source_index_kind` | Original frame/sample/control-step index, where available |
| `raw_signal`, `raw_signal_definition` | The method-specific signal and its meaning |
| `instantaneous_reward` | Effective increment computed by differencing the saved cumulative curve, with a preceding zero |
| `cumulative_reward` | Original unnormalized cumulative array |
| `normalized_cumulative_reward` | Original cumulative array divided by successful-rollout final return |
| `included_in_main_figure` | Whether the method belongs in that protocol's main comparison |

Native per-method NPZ files may retain a nonzero model output at reset. The consolidated cumulative protocol does not count this as a transition. CSV `instantaneous_reward` uses the effective cumulative increment and is therefore zero at reset in the native protocol. Small floating-point rounding differences can arise when differencing stored float32 cumulative arrays.

TemporalOT's normalized arrays are provided for traceability, but they were not plotted in the main adapted comparison. Its first saved transport cost is part of the native cost array; unlike the strict sixteen-point protocol, this raw cost sequence was not reset-zeroed.

For example, the exported JEV curves can be loaded directly:

```python
import numpy as np

with np.load("results/offline/adapted/jev.npz", allow_pickle=False) as data:
    success = data["success_normalized_cumulative_reward"]
    failure = data["failure_normalized_cumulative_reward"]
```

[PROVENANCE.json](../results/offline/PROVENANCE.json) records experiment-relative source filenames, original SHA-256 checksums, released-file checksums, and the collection dates recorded by the experiment identifiers. Metadata JSON files were rewritten to use relative paths; original NPZ and figure files were copied byte for byte. Credentials, API requests/responses, complete rollout states/videos, and checkpoints are excluded from this data package.
