---
title: Installation Guide
---

# Installation Guide

The kicad-autorouter requires **KiCad 10** or later. It uses KiCad's native pcbnew Python API and does not support earlier versions.

## Option 1: Plugin and Content Manager (Recommended)

The easiest way to install is through KiCad's built-in package manager.

1. Open KiCad
2. Go to **Plugin and Content Manager**
3. Click **Manage Repositories** → **Add**
4. Enter this repository URL:
   ```
   https://raw.githubusercontent.com/danwillis-aethl/kicad-autorouter/main/repository.json
   ```
5. Click **OK** to save
6. Search for **kicad-autorouter** in the plugin list
7. Click **Install**
8. Restart KiCad

Updates will appear in the Plugin and Content Manager when new versions are released.

## Option 2: Manual Installation

1. Clone or download the repository:
   ```bash
   git clone https://github.com/danwillis-aethl/kicad-autorouter.git
   ```

2. Copy the plugin files to your KiCad scripting directory:

   **Linux:**
   ```bash
   cp -r kicad_autorouter/ ~/.local/share/kicad/10/scripting/plugins/
   cp plugins/__init__.py ~/.local/share/kicad/10/scripting/plugins/kicad_autorouter_plugin.py
   ```

   **Windows:**
   ```
   Copy kicad_autorouter\ to %APPDATA%\kicad\10\scripting\plugins\
   Copy plugins\__init__.py to %APPDATA%\kicad\10\scripting\plugins\kicad_autorouter_plugin.py
   ```

   **macOS:**
   ```bash
   cp -r kicad_autorouter/ ~/Library/Application\ Support/kicad/10/scripting/plugins/
   cp plugins/__init__.py ~/Library/Application\ Support/kicad/10/scripting/plugins/kicad_autorouter_plugin.py
   ```

3. Restart KiCad

## Verifying Installation

After installation, open a PCB in KiCad's PCB Editor. You should see an **Autorouter** button in the toolbar. You can also access it via **Tools → External Plugins → Autorouter**.

If the button doesn't appear, check the KiCad scripting console (**Tools → Scripting Console**) for error messages. The most common issue is Python version mismatch — KiCad 10 requires the plugin to run under its bundled Python interpreter.

## Uninstalling

If installed via PCM, use the Plugin and Content Manager to uninstall. For manual installations, delete the `kicad_autorouter/` directory and the plugin registration file from your KiCad plugins directory.
