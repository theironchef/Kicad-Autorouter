---
title: Home
---

# kicad-autorouter

A pure Python autorouter for KiCad 10, ported from [Freerouting](https://github.com/freerouting/freerouting). No Java required.

This plugin integrates directly with KiCad's PCB Editor, reading your board via the pcbnew API and writing routed traces back — no file export/import needed.

## Features

- **Maze-based autorouting** with A* pathfinding and multi-pass ripup-and-reroute
- **Trace shoving** to push existing routes aside for new connections
- **45-degree routing** with snap-to-45° trace segments
- **Post-route optimization** including pull-tight, corner smoothing, and via removal
- **BGA/QFP fanout** for automatic pad escape routing
- **Design rule checking** with clearance, connectivity, and board-edge validation
- **Interactive routing** — route only selected nets
- **Selective re-routing** — rip up and re-route specific nets
- **Pre-commit DRC validation** — automatic rollback if routing introduces errors
- **Differential pair support** with auto-detection and length matching
- **Comprehensive configuration** with 25+ tunable routing parameters

## Quick Start

Install via KiCad's Plugin and Content Manager:

1. Open KiCad → **Plugin and Content Manager**
2. Click **Manage Repositories** → **Add**
3. Enter: `https://raw.githubusercontent.com/danwillis-aethl/kicad-autorouter/main/repository.json`
4. Search for "kicad-autorouter" and click **Install**
5. Restart KiCad

Then open a PCB, click the **Autorouter** toolbar button, and hit **Route**.

See the [Installation Guide](installation) for manual installation and other options.

## Documentation

- [Installation Guide](installation) — install via PCM or manually
- [User Guide](user-guide) — how to use the autorouter day-to-day
- [Configuration Reference](configuration) — all routing parameters explained
- [Architecture](architecture) — how the code is organized and how routing works
- [API Reference](api-reference) — module-level Python API for developers
- [Troubleshooting](troubleshooting) — common issues and solutions
- [Roadmap](roadmap) — what's done, what's next, and what's not planned

## Issues & Feature Requests

Found a bug or want to suggest a feature? [Open an issue on GitHub](https://github.com/danwillis-aethl/kicad-autorouter/issues).

When reporting a bug, please include your KiCad version, OS, and steps to reproduce. For feature requests, describe the use case and how you'd expect it to work.

## Links

- [GitHub Repository](https://github.com/danwillis-aethl/kicad-autorouter)
- [Issue Tracker](https://github.com/danwillis-aethl/kicad-autorouter/issues)
- [Freerouting (original Java project)](https://github.com/freerouting/freerouting)
