"""KiCad pcbnew API bridge - read board data and write routing results.

KiCadBoardReader and KiCadBoardWriter require pcbnew (KiCad's Python API)
which is only available inside KiCad or its Docker images.  Imports are
lazy so that the rest of the package works without pcbnew installed.
"""


def __getattr__(name: str):
    if name == "KiCadBoardReader":
        from kicad_autorouter.io.kicad_reader import KiCadBoardReader
        return KiCadBoardReader
    if name == "KiCadBoardWriter":
        from kicad_autorouter.io.kicad_writer import KiCadBoardWriter
        return KiCadBoardWriter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["KiCadBoardReader", "KiCadBoardWriter"]
