# 🦎 Chameleon Twist Level Editor

The **Chameleon Twist Level Editor** moves away from manual C-file editing, providing a structured Blender -> JSON -> C generation workflow. It leverages a Python-based code generation system to bridge the gap between Blender (using This Addon for collision and a modified Fast64 for visuals) and the N64’s native data structures.

## Installation & Setup

This repository is designed to run within the `tools/` directory of the main Chameleon Twist decompilation project.

1.  **Create the folder structure to be used:**
    ```bash
    mkdir [decomp-root]/tools/LevelEditor/
    ```
    - Add codegen.py to the folder
2.  **Install the plugin to blender 4.1:**
    - load blender 4.1
    - "CRTL" + "," to load plugins menu
    - import the .zip folder from the Releases Tab
3.  **Integrate with Build System:**
    The editor is automatically invoked when running the configuration script with the `--mod` flag.
    ```bash
    ./configure --mod --clean
    ninja
    ```
