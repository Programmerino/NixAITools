import json
import subprocess
import sys
import os
# import re # No longer needed
from argparse import ArgumentParser
from pathlib import Path
from typing import List, Tuple, Set

# --- Configuration ---
CUDA_MARKER = "wants-cuda"
OPENGL_DIR = Path("/run/opengl-driver")
DEV_DIR = Path("/dev")
NIX_CMD = "nix"

# --- Argument Parsing ---
parser = ArgumentParser(
    description="Nix pre-build hook to conditionally add CUDA-related sandbox paths."
)
parser.add_argument(
    "derivation_path",
    help="Path to the derivation file (.drv) or a string containing the path.",
)
parser.add_argument(
    "sandbox_path",
    nargs="?",
    help="Optional sandbox path (unused by this script).",
)

# --- Logging Functions ---
def log_info(message: str): print(f"Info: {message}", file=sys.stderr)
def log_warning(message: str): print(f"Warning: {message}", file=sys.stderr)
def log_error(message: str): print(f"Error: {message}", file=sys.stderr)

# --- Helper Functions ---
def safe_resolve(p: Path) -> Path | None:
    """Resolve symlinks to final target, return None on error or if non-existent."""
    try:
        resolved = p.resolve(strict=True)
        return resolved
    except (FileNotFoundError, RuntimeError, OSError) as e:
        if p.is_symlink():
             log_warning(f"Could not resolve symlink {p}: {e}")
        return None

def get_store_path_parent(p: Path) -> Path | None:
    """Given a path like /nix/store/xxx-name/sub/file, return /nix/store/xxx-name."""
    store_path_str = p.as_posix()
    if not store_path_str.startswith("/nix/store/"): return None
    parts = store_path_str.split('/')
    if len(parts) >= 4 and '-' in parts[3]:
        return Path('/', parts[1], parts[2], parts[3])
    else:
        log_warning(f"Could not extract Nix store path pattern from: {p}")
        return None

# --- Core Logic ---
def gather_potential_cuda_paths() -> Set[Path]:
    """
    Gathers essential paths for CUDA: devices, driver symlink, and relevant driver store paths.
    Returns a set of unique, absolute paths for bind mounting.
    """
    all_paths_to_bind: Set[Path] = set()
    required_store_paths: Set[Path] = set() # Store paths needed based on library targets

    # 1. Add device nodes
    if DEV_DIR.is_dir():
        dev_nodes = list(DEV_DIR.glob("video*")) + list(DEV_DIR.glob("nvidia*"))
        log_info(f"Checking {len(dev_nodes)} potential device nodes in {DEV_DIR}...")
        found_dev_nodes = 0
        for p in dev_nodes:
             if p.exists() or p.is_symlink():
                 # Add original path always (safer for direct references)
                 all_paths_to_bind.add(p.absolute())
                 found_dev_nodes += 1
                 log_info(f"  Adding device node: {p}")
                 # Try to resolve and add target if different (e.g. nvidia0 -> nvidiactl)
                 resolved_dev = safe_resolve(p)
                 if resolved_dev and resolved_dev.absolute() != p.absolute():
                     all_paths_to_bind.add(resolved_dev.absolute())
                     log_info(f"    -> Also adding resolved target: {resolved_dev}")

        if found_dev_nodes == 0:
             log_warning(f"No device nodes found matching video* or nvidia* in {DEV_DIR}")
    else:
        log_warning(f"Device directory not found or not accessible: {DEV_DIR}")

    # 2. Add the top-level driver directory/symlink itself (/run/opengl-driver)
    #    AND its resolved target (if it's a symlink pointing to the store).
    driver_path_added = False
    if OPENGL_DIR.is_symlink():
         if os.access(OPENGL_DIR, os.R_OK): # Check link readability
            log_info(f"Adding driver symlink itself: {OPENGL_DIR}")
            all_paths_to_bind.add(OPENGL_DIR.absolute())
            driver_path_added = True
            # Now, resolve THIS symlink and add its target if it's a store path
            opengl_dir_target = safe_resolve(OPENGL_DIR)
            if opengl_dir_target:
                 log_info(f"  -> Resolved target: {opengl_dir_target}")
                 # Check if the target looks like a store path
                 if opengl_dir_target.as_posix().startswith("/nix/store/"):
                      log_info(f"  -> Target is a store path, adding to required store paths.")
                      required_store_paths.add(opengl_dir_target.absolute())
                 else:
                      # If target isn't a store path, maybe still add it? Let's add it just in case.
                      log_info(f"  -> Target is not a store path, adding directly to binds.")
                      all_paths_to_bind.add(opengl_dir_target.absolute())

            else:
                 log_warning(f"  -> Could not resolve target of {OPENGL_DIR}")
         else:
              log_warning(f"Driver symlink {OPENGL_DIR} found but not readable, skipping.")

    elif OPENGL_DIR.is_dir():
         if os.access(OPENGL_DIR, os.R_OK):
             log_info(f"Adding driver directory itself: {OPENGL_DIR}")
             all_paths_to_bind.add(OPENGL_DIR.absolute())
             driver_path_added = True
         else:
             log_warning(f"Driver directory {OPENGL_DIR} found but not readable, skipping.")
    # else: # No need to log if not found unless debugging
    #     log_warning(f"Driver path {OPENGL_DIR} not found or not accessible.")


    # 3. Find additional required Nix store paths by scanning libs inside /run/opengl-driver/lib
    opengl_lib_dir = OPENGL_DIR / "lib"
    if driver_path_added and opengl_lib_dir.is_dir():
        libs_found_count = 0
        resolved_libs_count = 0
        log_info(f"Scanning {opengl_lib_dir} for driver library targets...")
        for p in opengl_lib_dir.glob("lib*"):
             # Only care about symlinks potentially pointing to the store
             if p.is_symlink():
                 # Check common prefixes
                 if p.name.startswith(("libcuda", "libnvidia", "libnv", "libEGL", "libGLES", "libGLX")):
                     libs_found_count += 1
                     target = safe_resolve(p) # Get ultimate target
                     if target and target.as_posix().startswith("/nix/store/"):
                         resolved_libs_count += 1
                         store_parent = get_store_path_parent(target)
                         # Add the store path containing the actual library
                         if store_parent and store_parent not in required_store_paths:
                             log_info(f"  Identified required store path: {store_parent} (from {p.name})")
                             required_store_paths.add(store_parent)

        log_info(f"Found {libs_found_count} potentially relevant lib symlinks, resolved {resolved_libs_count} to store paths.")
        if not required_store_paths:
             # This might be okay if the main OPENGL_DIR target already covered everything
             log_info(f"No *additional* required Nix store paths identified from libraries in {opengl_lib_dir}")

    elif driver_path_added:
         log_warning(f"Driver path {OPENGL_DIR} added, but library directory {opengl_lib_dir} not found or not accessible.")

    # Add the combined set of required store paths to the main bind list
    log_info(f"Adding {len(required_store_paths)} unique store paths identified.")
    all_paths_to_bind.update(required_store_paths)

    log_info(f"Collected {len(all_paths_to_bind)} unique absolute paths for potential binding.")
    return all_paths_to_bind


# --- Derivation Check & Main Execution ---
# (check_derivation_features remains the same)
def check_derivation_features(drv_path_str: str) -> bool:
    log_info(f"Checking derivation features via '{NIX_CMD} show-derivation {drv_path_str}'")
    try:
        proc = subprocess.run(
            [NIX_CMD, "show-derivation", drv_path_str],
            capture_output=True, check=True, text=True, encoding='utf-8', stderr=subprocess.PIPE
        )
        drv_data = json.loads(proc.stdout)
        if drv_path_str not in drv_data:
            log_error(f"Derivation path {drv_path_str} key not found in {NIX_CMD} show-derivation output."); return False
        drv_info = drv_data[drv_path_str]
        features = drv_info.get("env", {}).get("requiredSystemFeatures", [])
        if "cuda" in features:
            log_info(f"Found 'cuda' in requiredSystemFeatures of {drv_path_str}"); return True
        else:
            log_info(f"Did not find 'cuda' in requiredSystemFeatures of {drv_path_str}"); return False
    except FileNotFoundError: log_error(f"'{NIX_CMD}' command not found."); return False
    except subprocess.CalledProcessError as e:
        log_error(f"Command '{e.cmd}' failed with exit code {e.returncode}.");
        if e.stderr: log_error(f"Nix stderr: {e.stderr.strip()}")
        else: log_error("Nix command produced no stderr."); return False
    except json.JSONDecodeError as e: log_error(f"Failed to parse JSON output: {e}"); return False
    except Exception as e: log_error(f"Unexpected error checking derivation {drv_path_str}: {e}"); return False

if __name__ == "__main__":
    args = parser.parse_args()
    drv_path_str: str = args.derivation_path
    drv_path = Path(drv_path_str)

    needs_cuda_bindings = False
    checked_features_successfully = False

    if drv_path.is_file():
        if os.access(drv_path, os.R_OK):
            log_info(f"Derivation file found and readable: {drv_path_str}. Checking features.")
            needs_cuda_bindings = check_derivation_features(drv_path_str)
            checked_features_successfully = True
        else: log_warning(f"Derivation file found but not readable: {drv_path_str}. Falling back.")
    elif drv_path.exists(): log_warning(f"Path exists but is not a file: {drv_path_str}. Falling back.")
    else: log_info(f"Derivation file not found: {drv_path_str}. Falling back.")

    if not checked_features_successfully:
        log_info(f"Checking path name for marker: '{CUDA_MARKER}'.")
        if CUDA_MARKER in drv_path_str:
            log_info(f"Found '{CUDA_MARKER}' marker."); needs_cuda_bindings = True
        else:
            log_info(f"Marker '{CUDA_MARKER}' not found."); needs_cuda_bindings = False

    if needs_cuda_bindings:
        log_info("CUDA bindings needed. Gathering paths...")
        paths_to_bind = gather_potential_cuda_paths()

        valid_binds: List[Tuple[str, str]] = []
        if paths_to_bind:
            for p in sorted(list(paths_to_bind), key=lambda x: x.as_posix()):
                 if p.exists() or p.is_symlink():
                     p_str = p.as_posix()
                     valid_binds.append((p_str, p_str))
                 else:
                     log_warning(f"Skipping non-existent path during final bind list creation: {p}")
        else:
             log_warning("Path gathering resulted in an empty set.")


        if not valid_binds:
             log_warning("No valid paths found to bind mount for CUDA.")
             # Exit without printing anything, build proceeds without extra mounts
             sys.exit(0)

        log_info(f"Adding {len(valid_binds)} paths to sandbox:")
        for guest, host in valid_binds: log_info(f"  {guest} -> {host}")

        print("extra-sandbox-paths")
        for guest_path, host_path in valid_binds: print(f"{guest_path}={host_path}")
        print()

    else:
        log_info("No CUDA bindings required.")
        sys.exit(0)
