#!/usr/bin/env python3
"""Export a completed run without writing to the run or contacting any service."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time


BIN_STEPS = 10_000
POLL_SECONDS = 45
EVAL_EPISODES = 10
EPISODE_FIELDS = (
    "step", "episode", "episode_length", "process_return", "success_return",
    "episode_return", "native_success",
)
EVALUATION_FIELDS = ("step", "episodes", "successes", "success_rate", "mean_length")
INTEGER_FIELDS = {"step", "episode", "episode_length", "native_success"}


class ExportError(Exception):
    """A run cannot safely be published as final results."""


class IncompleteRun(ExportError):
    pass


def read_json(run_dir: Path, name: str):
    try:
        return json.loads((run_dir / name).read_bytes())
    except (OSError, ValueError) as exc:
        raise ExportError(f"Cannot read valid {name}; no final results exported.") from exc


def integer(value, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ExportError(f"{label} must be an integer.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportError(f"{label} must be an integer.") from exc
    if not math.isfinite(number) or not number.is_integer() or number < minimum:
        raise ExportError(f"Invalid {label}.")
    return int(number)


def finite(value, label: str) -> float:
    if isinstance(value, bool):
        raise ExportError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ExportError(f"{label} must be a finite number.")
    return number


def evaluation_row(row: dict) -> dict:
    if not isinstance(row, dict) or not all(k in row for k in EVALUATION_FIELDS):
        raise ExportError("Evaluation is missing required numeric fields.")
    result = {
        k: (integer(row[k], f"evaluation.{k}") if k in {"step", "episodes", "successes"}
            else finite(row[k], f"evaluation.{k}"))
        for k in EVALUATION_FIELDS
    }
    if result["episodes"] != EVAL_EPISODES:
        raise ExportError("Every evaluation must contain exactly 10 episodes.")
    if result["successes"] > EVAL_EPISODES or result["mean_length"] <= 0:
        raise ExportError("Invalid evaluation success count or mean length.")
    if not math.isclose(result["success_rate"], result["successes"] / EVAL_EPISODES,
                        rel_tol=0, abs_tol=1e-9):
        raise ExportError("Evaluation success_rate disagrees with successes / episodes.")
    return result


def load_final(run_dir: Path) -> tuple[list[dict], list[dict], dict, dict]:
    if not (run_dir / "summary.json").is_file():
        raise IncompleteRun("summary.json is absent; training is incomplete. No final results exported.")
    summary = read_json(run_dir, "summary.json")
    if not isinstance(summary, dict) or summary.get("completed") is not True:
        raise IncompleteRun("summary.completed must be the JSON boolean true. No final results exported.")
    # Read only these four files. All source metadata is published as SHA-256 hashes.
    source_bytes = {}
    for name in ("summary.json", "config.json", "metrics.csv", "evaluation.json"):
        try:
            source_bytes[name] = (run_dir / name).read_bytes()
        except OSError as exc:
            raise ExportError(f"Required input {name} is unavailable.") from exc
    try:
        summary = json.loads(source_bytes["summary.json"])
        config = json.loads(source_bytes["config.json"])
        raw_evaluations = json.loads(source_bytes["evaluation.json"])
    except (ValueError, UnicodeError) as exc:
        raise ExportError("A required JSON input is invalid.") from exc
    if not isinstance(summary, dict) or summary.get("completed") is not True:
        raise IncompleteRun("Completion marker changed; no final results exported.")
    if not isinstance(config, dict):
        raise ExportError("config.json must be an object.")
    final_step = integer(summary.get("steps"), "summary.steps", minimum=1)
    if final_step != integer(config.get("total_steps"), "config.total_steps", minimum=1):
        raise ExportError("summary.steps does not match config.total_steps.")
    try:
        reader = csv.DictReader(io.StringIO(source_bytes["metrics.csv"].decode("utf-8")))
        if not reader.fieldnames or not all(k in reader.fieldnames for k in EPISODE_FIELDS):
            raise ExportError("metrics.csv is missing required episode fields.")
        episodes = []
        previous_step = 0
        previous_episode = -1
        for raw in reader:
            row = {
                k: (integer(raw[k], f"metrics.{k}") if k in INTEGER_FIELDS
                    else finite(raw[k], f"metrics.{k}"))
                for k in EPISODE_FIELDS
            }
            if (row["step"] <= previous_step or row["step"] > final_step
                    or row["episode"] <= previous_episode or row["episode_length"] < 1
                    or row["episode_length"] > row["step"] - previous_step
                    or row["native_success"] not in (0, 1)):
                raise ExportError("Episode steps, lengths, IDs, or success flags are inconsistent.")
            if not math.isclose(row["episode_return"], row["process_return"] + row["success_return"],
                                rel_tol=1e-9, abs_tol=1e-8):
                raise ExportError("Episode return disagrees with process + success returns.")
            episodes.append(row)
            previous_step, previous_episode = row["step"], row["episode"]
    except (UnicodeError, csv.Error) as exc:
        raise ExportError("metrics.csv is invalid.") from exc
    if not episodes or not isinstance(raw_evaluations, list) or not raw_evaluations:
        raise ExportError("Completed export requires recorded training episodes and evaluations.")
    if episodes[-1]["step"] != final_step:
        raise ExportError("Last metrics step must match the completed target step.")
    evaluations = [evaluation_row(row) for row in raw_evaluations]
    if any(a["step"] > b["step"] for a, b in zip(evaluations, evaluations[1:])):
        raise ExportError("Evaluation steps are out of order.")
    final_evaluation = evaluation_row(summary.get("final_evaluation"))
    if evaluations[-1]["step"] != final_step or final_evaluation != evaluations[-1]:
        raise ExportError("Final evaluation must match summary and the completed target step.")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()}
    return episodes, evaluations, {"completed": True, "steps": final_step}, hashes


def bin_rewards(episodes: list[dict]) -> list[dict]:
    bins = {}
    for row in episodes:
        # Right-closed bins: (0, 10000], (10000, 20000], ... . An episode
        # belongs to the bin containing its end step; its whole length is its weight.
        start = ((row["step"] - 1) // BIN_STEPS) * BIN_STEPS
        bucket = bins.setdefault(start, {
            "bin_start_step": start, "bin_end_step": start + BIN_STEPS,
            "episode_count": 0, "recorded_steps": 0,
            "process_return": 0.0, "total_return": 0.0,
        })
        bucket["episode_count"] += 1
        bucket["recorded_steps"] += row["episode_length"]
        bucket["process_return"] += row["process_return"]
        bucket["total_return"] += row["episode_return"]
    result = []
    for start in sorted(bins):
        bucket = bins[start]
        bucket["process_reward_per_step"] = bucket["process_return"] / bucket["recorded_steps"]
        bucket["total_reward_per_step"] = bucket["total_return"] / bucket["recorded_steps"]
        result.append(bucket)
    return result


def write_csv(path: Path, rows: list[dict], fields) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def make_figure(path: Path, bins: list[dict], evaluations: list[dict]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except ImportError as exc:
        raise ExportError("Install requirements-results.txt before exporting the final figure.") from exc
    fig, axes = plt.subplots(3, 1, figsize=(9, 9), constrained_layout=True)
    x = [row["bin_end_step"] / 1000 for row in bins]
    for ax, field, title, color in (
        (axes[0], "process_reward_per_step", "JEV process reward per step", "#2463A8"),
        (axes[1], "total_reward_per_step", "Total reward per step (including success bonus)", "#C16A24"),
    ):
        ax.plot(x, [row[field] for row in bins], color=color, marker="o", markersize=3)
        ax.axhline(0, color="#777777", linewidth=0.7, alpha=0.5)
        ax.set_title(title)
        ax.set_ylabel("Reward / recorded step")
    axes[2].plot([row["step"] / 1000 for row in evaluations],
                 [row["success_rate"] for row in evaluations],
                 color="#32815A", marker="o", markersize=3)
    axes[2].set_title("Evaluation success rate (10 episodes per checkpoint)")
    axes[2].set_ylabel("Success rate")
    axes[2].set_ylim(-0.025, 1.025)
    for ax in axes:
        ax.set_xlabel("Environment steps (thousands)")
        ax.grid(alpha=0.2)
    fig.suptitle("Final training results — one training seed, no multi-seed confidence intervals", fontsize=11)
    fig.savefig(path, dpi=180, metadata={"Software": "export_training_results.py"})
    plt.close(fig)


def export_final(run_dir: Path, repo_dir: Path) -> dict:
    episodes, evaluations, summary, hashes = load_final(run_dir)
    bins = bin_rewards(episodes)
    summary.update({
        "training_seed_count": 1,
        "multi_seed_confidence_intervals": False,
        "evaluation_episodes_per_checkpoint": EVAL_EPISODES,
        "recorded_training_episodes": len(episodes),
        "recorded_training_steps": sum(row["episode_length"] for row in episodes),
        "last_recorded_episode_step": episodes[-1]["step"],
        "unrecorded_tail_steps": summary["steps"] - episodes[-1]["step"],
        "process_reward_per_recorded_step": sum(r["process_return"] for r in episodes)
                                             / sum(r["episode_length"] for r in episodes),
        "total_reward_per_recorded_step": sum(r["episode_return"] for r in episodes)
                                           / sum(r["episode_length"] for r in episodes),
        "reward_bin_size_steps": BIN_STEPS,
        "reward_bin_assignment": "right_closed_episode_end_step",
        "final_evaluation": evaluations[-1],
        "source_sha256": hashes,
    })
    destinations = {
        "training_episodes.csv": repo_dir / "results/online/training_episodes.csv",
        "training_bins_10k.csv": repo_dir / "results/online/training_bins_10k.csv",
        "evaluation.csv": repo_dir / "results/online/evaluation.csv",
        "evaluation.json": repo_dir / "results/online/evaluation.json",
        "training_final.png": repo_dir / "assets/figures/training_final.png",
        "final_summary.json": repo_dir / "results/online/final_summary.json",
    }
    if any(run_dir == p.resolve() or run_dir in p.resolve().parents for p in destinations.values()):
        raise ExportError("Output would modify the source run directory; choose a separate repository.")
    repo_dir.mkdir(parents=True, exist_ok=True)
    # Render everything first; invalid inputs or a failed plot never create final artifacts.
    with tempfile.TemporaryDirectory(prefix=".final-export-", dir=repo_dir) as temporary:
        stage = Path(temporary)
        write_csv(stage / "training_episodes.csv", episodes, EPISODE_FIELDS)
        write_csv(stage / "training_bins_10k.csv", bins, tuple(bins[0]))
        write_csv(stage / "evaluation.csv", evaluations, EVALUATION_FIELDS)
        write_json(stage / "evaluation.json", evaluations)
        make_figure(stage / "training_final.png", bins, evaluations)
        summary["artifact_sha256"] = {
            name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
            for name in destinations if name != "final_summary.json"
        }
        write_json(stage / "final_summary.json", summary)
        for name, destination in destinations.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / name, destination)
    return summary


def last_recorded_step(run_dir: Path) -> int:
    try:
        with (run_dir / "metrics.csv").open(newline="") as handle:
            return max((int(row["step"]) for row in csv.DictReader(handle)), default=0)
    except (OSError, ValueError, KeyError):
        return 0


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        stat_path = Path("/proc") / str(pid) / "stat"
        if stat_path.exists() and stat_path.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return False
        return True
    except PermissionError:
        return True
    except (OSError, IndexError):
        return False


def trainer_alive(run_dir: Path, explicit_pid: int | None) -> bool | None:
    if explicit_pid is not None:
        return pid_alive(explicit_pid)
    status_path = run_dir / "auto_resume_status.json"
    try:
        status = json.loads(status_path.read_bytes())
        pid = status.get("trainer_pid")
        if type(pid) is int and pid > 0 and pid_alive(pid):
            cmdline = Path("/proc") / str(pid) / "cmdline"
            if not cmdline.exists() or str(run_dir).encode() in cmdline.read_bytes().split(b"\0"):
                return True
    except (OSError, ValueError, AttributeError):
        pass
    proc = Path("/proc")
    if proc.is_dir():
        for child in proc.iterdir():
            if not child.name.isdecimal() or int(child.name) == os.getpid():
                continue
            try:
                args = (child / "cmdline").read_bytes().split(b"\0")
                if b"--output-dir" in args and str(run_dir).encode() in args and pid_alive(int(child.name)):
                    return True
            except OSError:
                continue
        return False
    return False if status_path.exists() else None


def current_api_stop(run_dir: Path) -> bool:
    try:
        marker = json.loads((run_dir / "stopped_api_error.json").read_bytes())
        step = marker.get("step")
        return (marker.get("completed") is False and type(step) is int
                and step >= last_recorded_step(run_dir))
    except (OSError, ValueError, AttributeError):
        return False


def waiting_for_resume(run_dir: Path) -> bool:
    try:
        status = json.loads((run_dir / "auto_resume_status.json").read_bytes())
        return status.get("state") == "waiting_after_transient_api_failure"
    except (OSError, ValueError, AttributeError):
        return False


def wait_for_completion(run_dir: Path, explicit_pid: int | None) -> None:
    while True:
        try:
            summary = read_json(run_dir, "summary.json") if (run_dir / "summary.json").exists() else None
        except ExportError:
            summary = None  # The trainer may still be writing its completion marker.
        if isinstance(summary, dict) and summary.get("completed") is True:
            return
        alive = trainer_alive(run_dir, explicit_pid)
        if alive is False:
            if waiting_for_resume(run_dir):
                print("Waiting for transient API recovery; checking again in 45 seconds.", flush=True)
                time.sleep(POLL_SECONDS)
                continue
            if current_api_stop(run_dir):
                raise IncompleteRun("An active API failure/quota stop was recorded; no final results exported.")
            raise IncompleteRun("Trainer ended without completed=true; no final results exported.")
        if alive is None:
            raise IncompleteRun("Cannot track the trainer on this platform; use --watch-pid. No final results exported.")
        print("Training is incomplete; checking again in 45 seconds.", flush=True)
        time.sleep(POLL_SECONDS)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="Read-only source training directory.")
    parser.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Output repository root (default: the root containing this script).")
    parser.add_argument("--watch", action="store_true", help="Wait in the foreground; poll every 45 seconds.")
    parser.add_argument("--watch-pid", type=int, help="Optional trainer PID when automatic detection is unavailable.")
    args = parser.parse_args(argv)
    if args.watch_pid is not None and (not args.watch or args.watch_pid < 1):
        parser.error("--watch-pid requires --watch and a positive PID")
    try:
        run_dir, repo_dir = args.run_dir.resolve(), args.repo_dir.resolve()
        if args.watch:
            wait_for_completion(run_dir, args.watch_pid)
        result = export_final(run_dir, repo_dir)
    except IncompleteRun as exc:
        print(f"Incomplete: {exc}", file=sys.stderr)
        return 2
    except (ExportError, OSError) as exc:
        # Do not print raw source errors, API reasons, or filesystem paths.
        message = str(exc) if isinstance(exc, ExportError) else "Could not write final artifacts."
        print(f"Export failed: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Watcher interrupted; no completion was inferred.", file=sys.stderr)
        return 130
    print(f"Exported completed run: {result['steps']} steps; final evaluation "
          f"{result['final_evaluation']['successes']}/10. No GitHub upload was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
