"""
KiCad Action Plugin — kicad-autorouter

Registers a toolbar button in KiCad 10's PCB editor that launches the
autorouter. Simple Altium-style UX: pick a few options, hit Route.
"""

from __future__ import annotations

import logging
import os
import traceback

logger = logging.getLogger("kicad_autorouter")

try:
    import pcbnew
    HAS_PCBNEW = True
except ImportError:
    HAS_PCBNEW = False

if HAS_PCBNEW:
    import wx

    class AutorouterPlugin(pcbnew.ActionPlugin):
        """KiCad Action Plugin for the kicad-autorouter."""

        def defaults(self):
            self.name = "Autorouter"
            self.category = "Routing"
            self.description = (
                "Automatic PCB trace routing using maze-based search with "
                "multi-pass ripup-and-reroute optimization."
            )
            self.show_toolbar_button = True
            icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
            if os.path.exists(icon_path):
                self.icon_file_name = icon_path

        def Run(self):
            """Entry point when the user clicks the toolbar button."""
            try:
                self._run_autorouter()
            except Exception as e:
                logger.error("Autorouter failed: %s", e)
                traceback.print_exc()
                wx.MessageBox(
                    f"Autorouter failed:\n\n{e}\n\nSee console for details.",
                    "Autorouter Error",
                    wx.OK | wx.ICON_ERROR,
                )

        def _run_autorouter(self):
            """Main autorouter execution."""
            from kicad_autorouter.io.kicad_reader import KiCadBoardReader
            from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
            from kicad_autorouter.autoroute.batch import BatchAutorouter, AutorouteConfig
            from kicad_autorouter.optimize.pull_tight import PullTightAlgo
            from kicad_autorouter.optimize.via_optimize import ViaOptimizer

            # ----- Minimal config popup (Altium-style) -----
            config = self._show_config_popup()
            if config is None:
                return  # User cancelled

            board_obj = pcbnew.GetBoard()
            if board_obj is None:
                wx.MessageBox("No board open.", "Autorouter", wx.OK | wx.ICON_WARNING)
                return

            # ----- Progress bar in KiCad status area -----
            progress = wx.ProgressDialog(
                "Autorouter",
                "Reading board...",
                maximum=100,
                style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_CAN_ABORT,
            )

            try:
                # Step 1: Read board
                progress.Update(5, "Reading board...")
                reader = KiCadBoardReader()
                board = reader.read_from_editor()

                # Step 2: Run autorouter
                def on_progress(msg: str, frac: float):
                    percent = int(10 + frac * 70)
                    keep_going, _ = progress.Update(percent, msg)
                    if not keep_going:
                        raise InterruptedError("Cancelled")

                ar_config = AutorouteConfig(
                    max_passes=config["max_passes"],
                    time_limit_seconds=config["time_limit"],
                    progress_callback=on_progress,
                )

                autorouter = BatchAutorouter(board, board.design_rules, ar_config)
                result = autorouter.run()

                # Step 3: Optimize
                progress.Update(82, "Optimizing traces...")
                pull_tight = PullTightAlgo(board, board.design_rules)
                pull_tight.optimize_all()

                progress.Update(90, "Removing redundant vias...")
                via_opt = ViaOptimizer(board, board.design_rules)
                via_opt.optimize_all()

                # Step 4: Write results back
                progress.Update(95, "Writing results...")
                writer = KiCadBoardWriter(reader.layer_map_reverse)
                items_written = writer.write_to_editor(board)

                progress.Update(100, "Done!")

                # Results summary
                pct = result.completion_percentage
                wx.MessageBox(
                    f"Routed {result.connections_routed}/{result.total_connections} "
                    f"({pct:.0f}%) in {result.elapsed_seconds:.1f}s\n"
                    f"Passes: {result.passes_run}  |  Items added: {items_written}",
                    "Autorouter",
                    wx.OK | wx.ICON_INFORMATION,
                )

            except InterruptedError:
                wx.MessageBox(
                    "Autorouting cancelled.",
                    "Autorouter",
                    wx.OK | wx.ICON_INFORMATION,
                )
            finally:
                progress.Destroy()

        def _show_config_popup(self) -> dict | None:
            """Minimal config popup — just the essentials.

            Altium-style: a few options, one button. No separate window.
            """
            dlg = wx.Dialog(None, title="Autorouter", size=(300, 220))
            panel = wx.Panel(dlg)
            sizer = wx.BoxSizer(wx.VERTICAL)

            # Max passes
            row1 = wx.BoxSizer(wx.HORIZONTAL)
            row1.Add(
                wx.StaticText(panel, label="Max passes:"),
                0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
            )
            passes_spin = wx.SpinCtrl(panel, value="20", min=1, max=100, size=(70, -1))
            row1.Add(passes_spin, 0, wx.ALIGN_CENTER_VERTICAL)
            sizer.Add(row1, 0, wx.ALL, 10)

            # Time limit
            row2 = wx.BoxSizer(wx.HORIZONTAL)
            row2.Add(
                wx.StaticText(panel, label="Time limit (s):"),
                0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
            )
            time_spin = wx.SpinCtrl(panel, value="300", min=10, max=3600, size=(70, -1))
            row2.Add(time_spin, 0, wx.ALIGN_CENTER_VERTICAL)
            sizer.Add(row2, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            # Route button + cancel
            btn_sizer = wx.StdDialogButtonSizer()
            ok_btn = wx.Button(panel, wx.ID_OK, "Route")
            ok_btn.SetDefault()
            cancel_btn = wx.Button(panel, wx.ID_CANCEL)
            btn_sizer.AddButton(ok_btn)
            btn_sizer.AddButton(cancel_btn)
            btn_sizer.Realize()
            sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)

            panel.SetSizer(sizer)
            dlg.Fit()

            if dlg.ShowModal() == wx.ID_OK:
                config = {
                    "max_passes": passes_spin.GetValue(),
                    "time_limit": time_spin.GetValue(),
                }
                dlg.Destroy()
                return config

            dlg.Destroy()
            return None

    # Register with KiCad
    AutorouterPlugin().register()
