"""
Dynamic script loader for skills.

Loads and executes skill action scripts via importlib.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from loguru import logger


async def load_and_run(script_path: Path, params: dict) -> str:
    """Dynamically load a Python script and call its run() function.

    Args:
        script_path: Absolute path to the .py script.
        params: Keyword arguments to pass to run().

    Returns:
        String result from the script's run() function.

    Raises:
        FileNotFoundError: If the script does not exist.
        AttributeError: If the script has no run() function.
        RuntimeError: If execution fails.
    """
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    # Validate path is a .py file
    if script_path.suffix != ".py":
        raise ValueError(f"Not a Python script: {script_path}")

    try:
        # Load the module dynamically
        spec = importlib.util.spec_from_file_location(
            f"skill_script_{script_path.stem}",
            script_path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module from {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Check for run() function
        if not hasattr(module, "run"):
            raise AttributeError(
                f"Script {script_path} does not define a run() function."
            )

        run_fn = module.run

        # Call run() — handle both sync and async
        if inspect.iscoroutinefunction(run_fn):
            result = await run_fn(**params)
        else:
            result = run_fn(**params)

        return str(result) if result is not None else ""

    except (FileNotFoundError, AttributeError, ValueError):
        raise
    except Exception as e:
        raise RuntimeError(f"Error executing script {script_path}: {e}") from e
