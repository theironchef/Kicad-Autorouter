"""
DRC report export — KiCad JSON format and plain text.

Also provides unit conversion utilities for displaying violations
in user-friendly units.
"""

from __future__ import annotations

import json
from enum import Enum, auto

from kicad_autorouter.drc.violations import DrcResult, DrcViolation, Severity


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

class LengthUnit(Enum):
    """Length units for DRC reporting."""

    NANOMETERS = auto()
    MICROMETERS = auto()
    MILLIMETERS = auto()
    MILS = auto()       # thousandths of an inch
    INCHES = auto()


# Factors to convert FROM nanometers TO each unit
_NM_TO_UNIT = {
    LengthUnit.NANOMETERS: 1.0,
    LengthUnit.MICROMETERS: 1e-3,
    LengthUnit.MILLIMETERS: 1e-6,
    LengthUnit.MILS: 1.0 / 25_400,
    LengthUnit.INCHES: 1.0 / 25_400_000,
}

_UNIT_SUFFIX = {
    LengthUnit.NANOMETERS: "nm",
    LengthUnit.MICROMETERS: "µm",
    LengthUnit.MILLIMETERS: "mm",
    LengthUnit.MILS: "mil",
    LengthUnit.INCHES: "in",
}


def convert_nm(value_nm: float, unit: LengthUnit) -> float:
    """Convert a value in nanometers to the specified unit."""
    return value_nm * _NM_TO_UNIT[unit]


def format_length(value_nm: float, unit: LengthUnit, precision: int = 3) -> str:
    """Format a nanometer value as a string in the given unit."""
    converted = convert_nm(value_nm, unit)
    return f"{converted:.{precision}f}{_UNIT_SUFFIX[unit]}"


def format_position(x_nm: int, y_nm: int, unit: LengthUnit, precision: int = 3) -> str:
    """Format a board position as a string in the given unit."""
    x = convert_nm(x_nm, unit)
    y = convert_nm(y_nm, unit)
    suffix = _UNIT_SUFFIX[unit]
    return f"({x:.{precision}f}{suffix}, {y:.{precision}f}{suffix})"


# ---------------------------------------------------------------------------
# Plain text report
# ---------------------------------------------------------------------------

def export_text(result: DrcResult, unit: LengthUnit = LengthUnit.MILLIMETERS) -> str:
    """Export DRC results as a human-readable text report."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("DRC Report")
    lines.append("=" * 60)
    lines.append(f"Items checked: {result.board_items_checked}")
    lines.append(f"Nets checked:  {result.nets_checked}")
    lines.append(f"Time:          {result.elapsed_ms:.0f}ms")
    lines.append(f"Errors:        {result.error_count}")
    lines.append(f"Warnings:      {result.warning_count}")
    lines.append("")

    if not result.violations:
        lines.append("No violations found.")
    else:
        # Group by severity
        errors = [v for v in result.violations if v.severity == Severity.ERROR]
        warnings = [v for v in result.violations if v.severity == Severity.WARNING]

        if errors:
            lines.append(f"--- ERRORS ({len(errors)}) ---")
            for v in errors:
                lines.append(_format_violation(v, unit))
            lines.append("")

        if warnings:
            lines.append(f"--- WARNINGS ({len(warnings)}) ---")
            for v in warnings:
                lines.append(_format_violation(v, unit))
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def _format_violation(v: DrcViolation, unit: LengthUnit) -> str:
    pos = format_position(v.location.x, v.location.y, unit)
    layer = f" L{v.layer_index}" if v.layer_index >= 0 else ""
    parts = [f"  [{v.violation_type.name}]{layer} at {pos}"]
    parts.append(f"    {v.message}")
    if v.actual_value or v.required_value:
        actual = format_length(v.actual_value, unit)
        required = format_length(v.required_value, unit)
        parts.append(f"    Actual: {actual}, Required: {required}")
    if v.item_ids:
        parts.append(f"    Items: {', '.join(str(i) for i in v.item_ids)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# KiCad JSON report
# ---------------------------------------------------------------------------

def export_kicad_json(result: DrcResult) -> str:
    """Export DRC results in KiCad-compatible JSON format.

    Produces output similar to KiCad's DRC report JSON for compatibility
    with external tooling.
    """
    report = {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": "kicad-autorouter DRC",
        "coordinate_units": "mm",
        "violations": [],
        "unconnected_items": [],
        "schematic_parity": [],
    }

    for v in result.violations:
        entry = _violation_to_kicad_entry(v)
        if v.violation_type in (ViolationType.UNCONNECTED_ITEMS,
                                ViolationType.DISCONNECTED_NET_GROUP):
            report["unconnected_items"].append(entry)
        else:
            report["violations"].append(entry)

    return json.dumps(report, indent=2)


def _violation_to_kicad_entry(v: DrcViolation) -> dict:
    """Convert a DrcViolation to a KiCad JSON violation entry."""
    from kicad_autorouter.drc.violations import ViolationType  # local to avoid cycle

    severity_map = {
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "info",
    }

    entry: dict = {
        "type": v.violation_type.name.lower(),
        "severity": severity_map.get(v.severity, "error"),
        "description": v.message,
        "items": [],
    }

    # Position in mm
    pos = {
        "x": round(v.location.x / 1_000_000, 6),
        "y": round(v.location.y / 1_000_000, 6),
    }
    entry["pos"] = pos

    for item_id in v.item_ids:
        entry["items"].append({"uuid": str(item_id)})

    return entry


# Make ViolationType accessible for the JSON exporter
from kicad_autorouter.drc.violations import ViolationType  # noqa: E402
