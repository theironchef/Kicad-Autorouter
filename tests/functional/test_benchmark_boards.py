"""Benchmark tests against real-world Arduino KiCad boards.

These tests route real Arduino boards and record performance metrics
(timing, completion rate, via count, trace length, DRC violations)
to a JSON file for tracking over time.

Boards sourced from: https://github.com/sabogalc/KiCad-Arduino-Boards

Run with: pytest tests/functional/test_benchmark_boards.py -m functional -v
"""

import json
import os
import pathlib
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pytest

try:
    import pcbnew
    HAS_PCBNEW = True
except ImportError:
    HAS_PCBNEW = False

pytestmark = pytest.mark.functional

# Path to cloned Arduino boards repo (set by CI or env var)
ARDUINO_BOARDS_DIR = pathlib.Path(
    os.environ.get(
        "ARDUINO_BOARDS_DIR",
        str(pathlib.Path(__file__).parent.parent.parent / "arduino-boards"),
    )
)

METRICS_OUTPUT = pathlib.Path(
    os.environ.get(
        "METRICS_OUTPUT",
        str(pathlib.Path(__file__).parent.parent.parent / "benchmark-results.json"),
    )
)

# Board paths relative to the repo root's "KiCad Projects" dir
BOARD_CONFIGS = [
    {
        "name": "Arduino Nano",
        "path": "KiCad Projects/Arduino Nano/Arduino Nano.kicad_pcb",
        "complexity": "small",
    },
    {
        "name": "Arduino Uno R3",
        "path": "KiCad Projects/Uno/Arduino Uno/Arduino Uno.kicad_pcb",
        "complexity": "medium",
    },
    {
        "name": "Arduino Uno R3 SMD",
        "path": "KiCad Projects/Uno/Arduino Uno SMD/Arduino Uno SMD.kicad_pcb",
        "complexity": "medium",
    },
    {
        "name": "Arduino Leonardo",
        "path": "KiCad Projects/Arduino Leonardo/Arduino Leonardo.kicad_pcb",
        "complexity": "medium",
    },
    {
        "name": "Arduino Micro",
        "path": "KiCad Projects/Arduino Micro/Arduino Micro.kicad_pcb",
        "complexity": "medium",
    },
    {
        "name": "Arduino Mega 2560",
        "path": "KiCad Projects/Arduino Mega 2560/Arduino Mega 2560.kicad_pcb",
        "complexity": "large",
    },
]


@dataclass
class BoardMetrics:
    """Metrics collected from a single board routing run."""

    board_name: str
    complexity: str
    timestamp: str
    total_nets: int = 0
    total_pads: int = 0
    total_components: int = 0
    copper_layers: int = 0
    pre_route_errors: int = 0
    pre_route_warnings: int = 0
    connections_routed: int = 0
    connections_failed: int = 0
    total_connections: int = 0
    completion_pct: float = 0.0
    passes_run: int = 0
    routing_time_s: float = 0.0
    trace_count: int = 0
    via_count: int = 0
    total_trace_length_nm: float = 0.0
    drc_errors: int = 0
    drc_warnings: int = 0
    git_sha: str = ""
    git_branch: str = ""


def _get_git_info() -> tuple[str, str]:
    """Get current git SHA and branch."""
    sha = os.environ.get("GITHUB_SHA", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")

    if not sha:
        try:
            sha = (
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()[:12]
            )
        except Exception:
            sha = "unknown"

    if not branch:
        try:
            branch = (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except Exception:
            branch = "unknown"

    return sha, branch


def _run_benchmark(board_path: str, board_name: str, complexity: str) -> BoardMetrics:
    """Run full routing benchmark on a board and collect metrics."""
    from kicad_autorouter.autoroute.batch import AutorouteConfig, BatchAutorouter
    from kicad_autorouter.autoroute.pre_route_analysis import PreRouteAnalyzer
    from kicad_autorouter.drc.checker import DrcChecker, DrcConfig
    from kicad_autorouter.io.kicad_reader import KiCadBoardReader

    git_sha, git_branch = _get_git_info()
    metrics = BoardMetrics(
        board_name=board_name,
        complexity=complexity,
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=git_sha,
        git_branch=git_branch,
    )

    # Load board
    reader = KiCadBoardReader()
    board = reader.read(board_path)

    # Pre-route analysis
    analyzer = PreRouteAnalyzer(board)
    report = analyzer.analyze()
    metrics.total_nets = report.total_nets
    metrics.total_pads = report.total_pads
    metrics.total_components = report.components
    metrics.copper_layers = report.copper_layers
    metrics.total_connections = report.total_connections
    metrics.pre_route_errors = len(report.errors)
    metrics.pre_route_warnings = len(report.warnings)

    # Route
    config = AutorouteConfig(
        max_passes=20,
        time_limit_seconds=120.0,
    )
    start = time.monotonic()
    router = BatchAutorouter(board, board.design_rules, config)
    result = router.run()
    metrics.routing_time_s = round(time.monotonic() - start, 3)

    metrics.connections_routed = result.connections_routed
    metrics.connections_failed = result.connections_failed
    metrics.total_connections = result.total_connections
    metrics.completion_pct = round(result.completion_percentage, 2)
    metrics.passes_run = result.passes_run

    # Board quality
    score = board.compute_score()
    metrics.trace_count = score.trace_count
    metrics.via_count = score.via_count
    metrics.total_trace_length_nm = score.total_trace_length

    # DRC
    checker = DrcChecker(board, DrcConfig())
    drc = checker.run()
    metrics.drc_errors = drc.error_count
    metrics.drc_warnings = drc.warning_count

    return metrics


def _save_metrics(metrics_list: list[BoardMetrics]) -> None:
    """Append metrics to the benchmark results JSON file."""
    existing = []
    if METRICS_OUTPUT.exists():
        try:
            existing = json.loads(METRICS_OUTPUT.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []

    run = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "boards": [asdict(m) for m in metrics_list],
    }
    existing.append(run)
    METRICS_OUTPUT.write_text(json.dumps(existing, indent=2))


def _board_available(config: dict) -> bool:
    """Check if a board file exists."""
    return (ARDUINO_BOARDS_DIR / config["path"]).exists()


@pytest.mark.skipif(not HAS_PCBNEW, reason="pcbnew not available")
class TestBenchmarkBoards:
    """Benchmark routing against real Arduino boards."""

    @pytest.fixture(autouse=True, scope="class")
    def _collect_metrics(self):
        """Collect metrics across all board tests and save at end."""
        self.__class__._metrics = []
        yield
        if self.__class__._metrics:
            _save_metrics(self.__class__._metrics)

    @pytest.mark.parametrize(
        "board_cfg", BOARD_CONFIGS, ids=[b["name"] for b in BOARD_CONFIGS]
    )
    def test_route_board(self, board_cfg):
        """Route a real Arduino board and record metrics."""
        board_path = ARDUINO_BOARDS_DIR / board_cfg["path"]
        if not board_path.exists():
            pytest.skip(f"Board not found: {board_path}")

        metrics = _run_benchmark(
            str(board_path), board_cfg["name"], board_cfg["complexity"]
        )

        # Record metrics for later saving
        self.__class__._metrics.append(metrics)

        # Basic assertions — the router should at least load and attempt routing
        assert metrics.total_pads > 0, f"Board {board_cfg['name']} has no pads"
        assert metrics.total_nets > 0, f"Board {board_cfg['name']} has no nets"
        assert metrics.routing_time_s > 0, "Routing should take some time"

        # Print summary for CI logs
        print(f"\n{'='*60}")
        print(f"BENCHMARK: {metrics.board_name} ({metrics.complexity})")
        print(
            f"  Nets: {metrics.total_nets}, Pads: {metrics.total_pads}, "
            f"Layers: {metrics.copper_layers}"
        )
        print(
            f"  Connections: {metrics.connections_routed}/{metrics.total_connections} "
            f"({metrics.completion_pct}%)"
        )
        print(f"  Traces: {metrics.trace_count}, Vias: {metrics.via_count}")
        print(
            f"  DRC: {metrics.drc_errors} errors, {metrics.drc_warnings} warnings"
        )
        print(f"  Time: {metrics.routing_time_s}s in {metrics.passes_run} passes")
        print(f"{'='*60}")
