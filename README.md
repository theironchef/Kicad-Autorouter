# kicad-autorouter — Pure Python Autorouter for KiCad

A Python port of the [Freerouting](https://github.com/freerouting/freerouting) autorouter, built as a native KiCad 10 Action Plugin. No Java required.

> **Derived from Freerouting** — this project reimplements Freerouting's algorithms in Python as a KiCad plugin. The original Freerouting project was created by Alfons Wirtz and is maintained by the Freerouting community under the GPL-3.0 license.

## Status: v1.1.0

290 tests pass across unit, integration, and performance suites. v1.1 adds Situs-inspired features: pre-routing analysis, composable routing strategies, new optimization passes (spread, straighten, miter, hug, clean pad entries), post-route DRC cleanup, route-by-area, route-by-net-class, and differential pair fanout.

## Installation

### KiCad Plugin and Content Manager (Recommended)

1. Open KiCad → **Plugin and Content Manager**
2. Click **Manage Repositories** → **Add**
3. Enter: `https://raw.githubusercontent.com/danwillis-aethl/kicad-autorouter/main/repository.json`
4. Search for "kicad-autorouter" and click **Install**
5. Restart KiCad

### Manual Installation

Copy the `kicad_autorouter/` directory and `plugins/__init__.py` to your KiCad plugins directory:

- **Linux:** `~/.local/share/kicad/10/scripting/plugins/`
- **Windows:** `%APPDATA%\kicad\10\scripting\plugins\`
- **macOS:** `~/Library/Application Support/kicad/10/scripting/plugins/`

Then restart KiCad.

## Usage

1. Open a PCB in KiCad's PCB Editor
2. Place all components and define board outline
3. Click the **Autorouter** button in the toolbar (or **Tools → External Plugins → Autorouter**)
4. Set max passes and time limit, then click **Route**

## Documentation

Full docs at **[danwillis-aethl.github.io/kicad-autorouter](https://danwillis-aethl.github.io/kicad-autorouter/)**:

- [Installation Guide](https://danwillis-aethl.github.io/kicad-autorouter/installation)
- [User Guide](https://danwillis-aethl.github.io/kicad-autorouter/user-guide)
- [Configuration Reference](https://danwillis-aethl.github.io/kicad-autorouter/configuration)
- [Architecture](https://danwillis-aethl.github.io/kicad-autorouter/architecture)
- [API Reference](https://danwillis-aethl.github.io/kicad-autorouter/api-reference)
- [Troubleshooting](https://danwillis-aethl.github.io/kicad-autorouter/troubleshooting)
- [Roadmap](https://danwillis-aethl.github.io/kicad-autorouter/roadmap)

## Issues & Feature Requests

Found a bug or have an idea for a feature? Please open an issue on GitHub:

**[github.com/danwillis-aethl/kicad-autorouter/issues](https://github.com/danwillis-aethl/kicad-autorouter/issues)**

When reporting a bug, include your KiCad version, operating system, and steps to reproduce. If possible, attach or describe the board that triggered the issue. For feature requests, describe the use case and how you'd expect the feature to work.

## Attribution

Derived from [Freerouting](https://github.com/freerouting/freerouting), originally created by Alfons Wirtz and maintained by the Freerouting community.

## License

GPL-3.0 — see [LICENSE](LICENSE)
