"""
ValidatedRouter — Autorouter with pre-commit DRC validation.

Wraps the BatchAutorouter to add a DRC check after routing completes.
If DRC finds errors above the configured severity, the routed results
can be rejected (rolled back) before being committed to the board.

This ensures only clean routing results are presented to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto

from kicad_autorouter.autoroute.batch import (
    AutorouteConfig,
    AutorouteResult,
    BatchAutorouter,
)
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.history import BoardHistory
from kicad_autorouter.drc.checker import DrcChecker, DrcConfig
from kicad_autorouter.drc.violations import DrcResult, Severity
from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)


class CommitPolicy(Enum):
    """What to do when DRC finds violations after routing."""

    ALWAYS_COMMIT = auto()        # Commit even with errors (legacy behaviour)
    REJECT_ON_ERROR = auto()      # Rollback if any ERROR-severity violation
    REJECT_ON_WARNING = auto()    # Rollback if any WARNING or ERROR
    REJECT_ON_NEW_ERRORS = auto() # Rollback only if routing *added* new errors


@dataclass
class ValidatedResult:
    """Result of a validated routing run."""

    route_result: AutorouteResult
    drc_before: DrcResult | None = None
    drc_after: DrcResult | None = None
    committed: bool = False
    rollback_reason: str = ""

    @property
    def new_error_count(self) -> int:
        """Number of new DRC errors introduced by routing."""
        before = self.drc_before.error_count if self.drc_before else 0
        after = self.drc_after.error_count if self.drc_after else 0
        return max(0, after - before)

    @property
    def new_warning_count(self) -> int:
        """Number of new DRC warnings introduced by routing."""
        before = self.drc_before.warning_count if self.drc_before else 0
        after = self.drc_after.warning_count if self.drc_after else 0
        return max(0, after - before)


class ValidatedRouter:
    """Autorouter with pre-commit DRC validation.

    Runs a full DRC before routing (to establish a baseline), routes the
    board, runs DRC again, and then decides whether to commit or roll
    back based on the configured policy.
    """

    def __init__(
        self,
        board: RoutingBoard,
        rules: DesignRules,
        route_config: AutorouteConfig | None = None,
        drc_config: DrcConfig | None = None,
        policy: CommitPolicy = CommitPolicy.REJECT_ON_NEW_ERRORS,
    ):
        self.board = board
        self.rules = rules
        self.route_config = route_config
        self.drc_config = drc_config
        self.policy = policy
        self._history = BoardHistory(board)

    def run(self) -> ValidatedResult:
        """Route the board with DRC validation."""
        result = ValidatedResult(route_result=AutorouteResult())

        # 1) Run DRC on the current board to get baseline
        logger.info("Running pre-route DRC baseline...")
        drc_before = DrcChecker(self.board, self.drc_config).run()
        result.drc_before = drc_before
        logger.info(
            "Pre-route DRC: %d errors, %d warnings",
            drc_before.error_count, drc_before.warning_count,
        )

        # 2) Save board state in case we need to rollback
        self._history.snapshot("pre_route")

        # 3) Run the autorouter
        logger.info("Running autorouter...")
        router = BatchAutorouter(self.board, self.rules, self.route_config)
        route_result = router.run()
        result.route_result = route_result

        # 4) Run DRC on routed board
        logger.info("Running post-route DRC validation...")
        drc_after = DrcChecker(self.board, self.drc_config).run()
        result.drc_after = drc_after
        logger.info(
            "Post-route DRC: %d errors, %d warnings (was %d/%d)",
            drc_after.error_count, drc_after.warning_count,
            drc_before.error_count, drc_before.warning_count,
        )

        # 5) Decide whether to commit
        should_commit, reason = self._evaluate_policy(drc_before, drc_after)

        if should_commit:
            result.committed = True
            logger.info("DRC passed — routing committed")
        else:
            result.committed = False
            result.rollback_reason = reason
            logger.warning("DRC failed — rolling back routing: %s", reason)
            self._history.undo()

        return result

    def _evaluate_policy(
        self, before: DrcResult, after: DrcResult
    ) -> tuple[bool, str]:
        """Return (should_commit, reason_if_not)."""
        if self.policy == CommitPolicy.ALWAYS_COMMIT:
            return True, ""

        if self.policy == CommitPolicy.REJECT_ON_ERROR:
            if after.error_count > 0:
                return False, f"{after.error_count} DRC error(s) found"
            return True, ""

        if self.policy == CommitPolicy.REJECT_ON_WARNING:
            total = after.error_count + after.warning_count
            if total > 0:
                return False, (
                    f"{after.error_count} error(s) and "
                    f"{after.warning_count} warning(s) found"
                )
            return True, ""

        if self.policy == CommitPolicy.REJECT_ON_NEW_ERRORS:
            new_errors = after.error_count - before.error_count
            if new_errors > 0:
                return False, f"routing introduced {new_errors} new DRC error(s)"
            return True, ""

        return True, ""  # unknown policy — commit
