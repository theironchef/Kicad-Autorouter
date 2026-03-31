"""
KiCadBoardWriter — Write routing results back to KiCad 10's pcbnew API.

Takes new traces and vias from the RoutingBoard and adds them as a single
undo-able commit. Only writes items created by the autorouter.

Targets KiCad 10 only.
"""

from __future__ import annotations

import logging

from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.item import FixedState

logger = logging.getLogger(__name__)

import pcbnew


class KiCadBoardWriter:
    """Write autorouter results back to KiCad.

    Creates PCB_TRACK and PCB_VIA objects and adds them within a single
    commit transaction so the entire autoroute is one undo step.
    """

    def __init__(self, layer_map: dict[int, int] | None = None):
        """
        Args:
            layer_map: Our layer index -> KiCad layer ID.
                       If None, assumes standard 2-layer (F.Cu / B.Cu).
        """
        self._layer_map = layer_map or {0: pcbnew.F_Cu, 1: pcbnew.B_Cu}

    def write_to_editor(
        self,
        routing_board: RoutingBoard,
        new_trace_ids: set[int] | None = None,
        new_via_ids: set[int] | None = None,
    ) -> int:
        """Write routing results to the currently open KiCad board.

        All changes are wrapped in a single commit so the user can
        undo the entire autoroute in one step.

        Args:
            routing_board: Board with routing results.
            new_trace_ids: IDs of traces to write. None = write all
                           non-fixed traces (i.e. autorouter-created).
            new_via_ids: IDs of vias to write. None = write all
                         non-fixed vias.

        Returns:
            Number of items added to KiCad board.
        """
        pcb_board = pcbnew.GetBoard()
        items_added = 0

        # Start a commit so the entire autoroute is one undo step
        commit = pcb_board.BeginCommit()

        try:
            # Write traces
            for trace in routing_board.get_traces():
                if trace.fixed_state == FixedState.USER_FIXED:
                    continue  # pre-existing trace, skip
                if new_trace_ids is not None and trace.id not in new_trace_ids:
                    continue
                count = self._write_trace(pcb_board, trace)
                items_added += count

            # Write vias
            for via in routing_board.get_vias():
                if via.fixed_state == FixedState.USER_FIXED:
                    continue  # pre-existing via, skip
                if new_via_ids is not None and via.id not in new_via_ids:
                    continue
                if self._write_via(pcb_board, via):
                    items_added += 1

            # Commit all changes as one undo step
            pcb_board.PushCommit("Autorouter")
        except Exception:
            pcb_board.DropCommit()
            raise

        # Refresh display
        pcbnew.Refresh()

        logger.info("Wrote %d items to KiCad board", items_added)
        return items_added

    def _write_trace(self, pcb_board, trace: Trace) -> int:
        """Write a Trace as PCB_TRACK segments."""
        kicad_layer = self._layer_map.get(trace.layer_index)
        if kicad_layer is None:
            logger.warning("No KiCad layer for internal layer %d", trace.layer_index)
            return 0

        if len(trace.corners) < 2:
            return 0

        segments_added = 0
        for i in range(len(trace.corners) - 1):
            start = trace.corners[i]
            end = trace.corners[i + 1]

            pcb_track = pcbnew.PCB_TRACK(pcb_board)
            pcb_track.SetStart(pcbnew.VECTOR2I(start.x, start.y))
            pcb_track.SetEnd(pcbnew.VECTOR2I(end.x, end.y))
            pcb_track.SetWidth(trace.width)
            pcb_track.SetLayer(kicad_layer)

            # Set net
            if trace.net_code > 0:
                pcb_track.SetNetCode(trace.net_code)

            pcb_board.Add(pcb_track)
            segments_added += 1

        return segments_added

    def _write_via(self, pcb_board, via: Via) -> bool:
        """Write a Via as PCB_VIA."""
        pcb_via = pcbnew.PCB_VIA(pcb_board)
        pcb_via.SetPosition(pcbnew.VECTOR2I(via.position.x, via.position.y))
        pcb_via.SetWidth(via.diameter)
        pcb_via.SetDrill(via.drill)

        # Set via type
        pcb_via.SetViaType(pcbnew.VIATYPE_THROUGH)

        # Map layer pair
        top_kicad = self._layer_map.get(via.start_layer)
        bot_kicad = self._layer_map.get(via.end_layer)
        if top_kicad is None or bot_kicad is None:
            logger.warning(
                "No KiCad layer mapping for via layers %d-%d",
                via.start_layer, via.end_layer,
            )
            return False

        pcb_via.SetLayerPair(top_kicad, bot_kicad)

        # Set net
        if via.net_code > 0:
            pcb_via.SetNetCode(via.net_code)

        pcb_board.Add(pcb_via)
        return True
