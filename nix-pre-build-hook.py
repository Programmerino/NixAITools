import json
import subprocess
import sys
import os
from argparse import ArgumentParser
from pathlib import Path
from typing import List, Tuple, Set

# --- Configuration ---
CUDA_MARKER = "wants-cuda" # Used as fallback if drv inspection fails
OPENGL_DIR = Path("/run/opengl-driver") # Standard path for NVIDIA drivers symlink
DEV_DIR = Path("/dev")
NIX_CMD = "nix" # Assume nix command is in PATH

# --- Argument Parsing ---
parser = ArgumentParser(
    description="Nix pre-build hook to conditionally add CUDA/GPU related sandbox paths."
)
parser.add_argument(
    "derivation_path",
    help="Path to the derivation file (.drv) being built.",
)
parser.add_argument(
    "sandbox_path",
    nargs="?", # This argument is provided by Nix but often not needed by the hook itself
    help="Optional path to the sandbox directory (unused by this script).",
)

# --- Logging Functions (to stderr) ---
def log_info(message: str): print(f"Info [cuda-hook]: {message}", file=sys.stderr)
def log_warning(message: str): print(f"Warning [cuda-hook]: {message}", file=sys.stderr)
def log_error(message: str): print(f"Error [cuda-hook]: {message}", file=sys.stderr)

# --- Helper Functions ---
def safe_resolve(p: Path) -> Path | None:
    """Resolve symlinks recursively to final target, return None on error or if non-existent."""
    try:
        # Check existence before resolving if it's not a symlink
        if not p.is_symlink() and not p.exists():
             return None
        # strict=True requires the *target* to exist, which is usually what we want
        # Setting it to False would allow resolving broken symlinks, less useful here.
        resolved = p.resolve(strict=True)
        return resolved
    except (FileNotFoundError, RuntimeError, OSError) as e:
        # Only log warning if it was a symlink, as non-symlink non-existence is handled above
        if p.is_symlink():
             log_warning(f"Could not resolve symlink {p}: {e}")
        # else: # Non-symlink case, don't warn if simply non-existent
        #    pass
        return None

def get_store_path_parent(p: Path) -> Path | None:
    """Given a path like /nix/store/xxx-name/sub/file, return /nix/store/xxx-name."""
    store_prefix = "/nix/store/"
    p_str = p.as_posix()
    if not p_str.startswith(store_prefix): return None
    parts = p_str[len(store_prefix):].split('/')
    if len(parts) >= 1 and '-' in parts[0]: # Basic check for 'hash-name' pattern
        return Path(store_prefix, parts[0])
    else:
        log_warning(f"Could not extract Nix store path pattern from: {p}")
        return None

# --- Core Logic ---
def gather_potential_cuda_paths() -> Set[Path]:
    """
    Gathers essential paths for CUDA/GPU access: devices, driver symlink, and relevant driver store paths.
    Returns a set of unique, absolute paths intended for bind mounting.
    """
    all_paths_to_bind: Set[Path] = set()
    required_store_paths: Set[Path] = set() # Store paths needed based on library targets

    # 1. Add device nodes (/dev/nvidia*, /dev/dri/card* etc.)
    #    We add both the original path and its resolved target if it's a symlink.
    if DEV_DIR.is_dir():
        # Common patterns for NVIDIA and general DRM devices
        # Add more patterns if needed for specific hardware/drivers (e.g., /dev/fuse)
        dev_patterns = ["nvidia*", "dri/card*", "dri/renderD*", "nvhost*", "nvmap"]
        log_info(f"Searching for device nodes in {DEV_DIR} matching: {dev_patterns}")
        found_dev_nodes = 0
        for pattern in dev_patterns:
             for p in DEV_DIR.glob(pattern):
                 # Check if it exists or is a symlink (even if broken initially)
                 if p.exists() or p.is_symlink():
                     abs_p = p.absolute()
                     # Add original path always (safer for direct references in userspace)
                     all_paths_to_bind.add(abs_p)
                     found_dev_nodes += 1
                     log_info(f"  Adding potential device node: {abs_p}")
                     # Try to resolve and add target if different (e.g. nvidia0 -> nvidiactl)
                     resolved_dev = safe_resolve(p) # Use safe_resolve here
                     if resolved_dev:
                         abs_resolved = resolved_dev.absolute()
                         if abs_resolved != abs_p:
                             all_paths_to_bind.add(abs_resolved)
                             log_info(f"    -> Also adding resolved target: {abs_resolved}")
                     # else: # safe_resolve already logs warnings for broken symlinks
                     #    pass

        if found_dev_nodes == 0:
             log_warning(f"No device nodes found matching patterns in {DEV_DIR}. GPU access might fail.")
    else:
        log_warning(f"Device directory not found or not accessible: {DEV_DIR}")

    # 2. Add the top-level driver directory/symlink itself (/run/opengl-driver)
    #    AND its resolved target (if it's a symlink pointing to the store).
    driver_path_added = False
    if OPENGL_DIR.exists() or OPENGL_DIR.is_symlink():
        abs_opengl_dir = OPENGL_DIR.absolute()
        log_info(f"Adding driver path itself: {abs_opengl_dir} ({'symlink' if OPENGL_DIR.is_symlink() else 'directory' if OPENGL_DIR.is_dir() else 'other'})")
        all_paths_to_bind.add(abs_opengl_dir)
        driver_path_added = True

        # If it's a symlink, resolve it to find the actual driver store path
        if OPENGL_DIR.is_symlink():
            opengl_dir_target = safe_resolve(OPENGL_DIR)
            if opengl_dir_target:
                 abs_target = opengl_dir_target.absolute()
                 log_info(f"  -> Resolved target: {abs_target}")
                 # Check if the target looks like a top-level store path
                 store_parent = get_store_path_parent(abs_target)
                 if store_parent:
                      log_info(f"  -> Target's store path parent: {store_parent}. Adding to required store paths.")
                      required_store_paths.add(store_parent)
                 else:
                      # If target isn't a store path, maybe still add it directly?
                      # This could be /usr/lib/nvidia or similar in non-NixOS. Less critical for Nix builds.
                      log_info(f"  -> Target does not appear to be a Nix store path. Adding target directly just in case.")
                      all_paths_to_bind.add(abs_target)
            # else: # safe_resolve handles logging failure

    else:
        log_warning(f"Driver path {OPENGL_DIR} not found or not accessible. GPU driver libs might be missing.")


    # 3. Find *additional* required Nix store paths by scanning libs inside the driver path.
    #    This is crucial because /run/opengl-driver might link to one store path (e.g., for GL),
    #    but CUDA libs might be in *another* store path, linked from within the first one's lib dir.
    opengl_lib_dir = OPENGL_DIR / "lib" # Standard subdirectory
    if driver_path_added and opengl_lib_dir.is_dir():
        libs_found_count = 0
        resolved_libs_count = 0
        log_info(f"Scanning {opengl_lib_dir} for library symlinks pointing to Nix store...")
        # Look for common library patterns
        lib_patterns = ["libcuda*", "libnvidia*", "libnv*", "libEGL*", "libGLES*", "libGLX*", "libGL.*", "libvulkan*"]
        for pattern in lib_patterns:
             for p in opengl_lib_dir.glob(pattern):
                 # Only care about symlinks potentially pointing to the store
                 if p.is_symlink():
                     libs_found_count += 1
                     target = safe_resolve(p) # Get ultimate target
                     if target:
                          # Check if the *target library file* is inside the Nix store
                          if target.as_posix().startswith("/nix/store/"):
                             resolved_libs_count += 1
                             store_parent = get_store_path_parent(target)
                             # Add the *containing store path* (e.g., /nix/store/xxx-nvidia-driver)
                             if store_parent and store_parent not in required_store_paths:
                                 log_info(f"  Identified required store path: {store_parent} (from lib: {p.name} -> {target.name})")
                                 required_store_paths.add(store_parent)
                             elif store_parent:
                                 log_info(f"  Store path {store_parent} already identified (from lib: {p.name})")
                          # else: # Target is not in store, ignore
                          #    pass
                     # else: # safe_resolve handled logging error

        log_info(f"Scanned {libs_found_count} library symlinks matching patterns, resolved {resolved_libs_count} to Nix store targets.")
        if not required_store_paths and driver_path_added and OPENGL_DIR.is_symlink():
             # Check if we already added the main driver target earlier
             main_target_store_path = get_store_path_parent(safe_resolve(OPENGL_DIR)) if safe_resolve(OPENGL_DIR) else None
             if main_target_store_path:
                 log_info(f"No *additional* store paths found via libs, but main driver target {main_target_store_path} was already added.")
             else:
                log_warning(f"No required Nix store paths identified from libraries in {opengl_lib_dir}. This might be okay or indicate missing links.")

    elif driver_path_added:
         log_warning(f"Driver path {OPENGL_DIR} added, but library directory {opengl_lib_dir} not found or not accessible.")

    # Add the combined set of required store paths to the main bind list
    if required_store_paths:
        log_info(f"Adding {len(required_store_paths)} unique required store paths to bind list.")
        all_paths_to_bind.update(required_store_paths)

    log_info(f"Collected {len(all_paths_to_bind)} unique absolute paths for potential binding.")
    return all_paths_to_bind


# --- Derivation Check & Main Execution ---
def check_derivation_features(drv_path_str: str) -> bool:
    """Inspects the derivation using 'nix show-derivation' for 'cuda' in requiredSystemFeatures."""
    log_info(f"Checking derivation features via '{NIX_CMD} show-derivation {drv_path_str}'")
    try:
        # Correct subprocess call: Use capture_output=True, remove explicit stderr
        proc = subprocess.run(
            [NIX_CMD, "show-derivation", drv_path_str],
            capture_output=True, check=True, text=True, encoding='utf-8'
            # Removed: stderr=subprocess.PIPE (redundant with capture_output=True)
        )
        # Output is expected to be a JSON object where keys are derivation paths
        drv_data = json.loads(proc.stdout)
        # The key might be slightly different (e.g., due to symlinks?), so iterate
        if not drv_data:
            log_error(f"'{NIX_CMD} show-derivation' returned empty JSON data.")
            return False
        # Assume the first key is the relevant one, usually only one is returned
        actual_drv_path = list(drv_data.keys())[0]
        drv_info = drv_data[actual_drv_path]

        # Navigate the JSON structure to find requiredSystemFeatures
        features = drv_info.get("env", {}).get("requiredSystemFeatures", [])
        if "cuda" in features:
            log_info(f"Found 'cuda' in requiredSystemFeatures of {actual_drv_path}"); return True
        else:
            log_info(f"Did not find 'cuda' in requiredSystemFeatures of {actual_drv_path}"); return False
    except FileNotFoundError:
        log_error(f"'{NIX_CMD}' command not found. Ensure Nix is in PATH for the build environment."); return False
    except subprocess.CalledProcessError as e:
        log_error(f"Command '{e.cmd}' failed with exit code {e.returncode}.")
        # Use captured stderr
        if e.stderr: log_error(f"Nix stderr:\n{e.stderr.strip()}")
        else: log_error("Nix command produced no stderr.");
        return False
    except json.JSONDecodeError as e:
        log_error(f"Failed to parse JSON output from 'nix show-derivation': {e}")
        log_error(f"Raw stdout was:\n{proc.stdout}") # Log raw output for debugging
        return False
    except Exception as e:
        # Catch any other unexpected errors during inspection
        log_error(f"Unexpected error checking derivation {drv_path_str}: {e}"); return False

if __name__ == "__main__":
    args = parser.parse_args()
    drv_path_str: str = args.derivation_path
    # Use Path object for easier handling, though the string is passed to nix command
    drv_path = Path(drv_path_str)

    needs_cuda_bindings = False
    checked_features_successfully = False

    # --- Determine if CUDA bindings are needed ---
    # 1. Preferred method: Inspect the derivation file
    if drv_path.is_file():
        # Check read permissions before trying to inspect
        if os.access(drv_path, os.R_OK):
            log_info(f"Derivation file found and readable: {drv_path_str}. Checking features.")
            # Check features, store if successful
            check_result = check_derivation_features(drv_path_str)
            # Only trust the result if no error occurred during the check
            if check_result is not None: # Assume function returns None on error now
                 needs_cuda_bindings = check_result
                 checked_features_successfully = True
            # else: Error already logged by check_derivation_features
        else:
            log_warning(f"Derivation file found but not readable: {drv_path_str}. Falling back to name check.")
    # Handle cases where the path exists but isn't a file (unlikely for .drv)
    elif drv_path.exists():
         log_warning(f"Path exists but is not a file: {drv_path_str}. Falling back to name check.")
    # Handle case where .drv file doesn't exist (might happen in some build scenarios?)
    else:
         log_info(f"Derivation file not found: {drv_path_str}. Falling back to name check.")

    # 2. Fallback method: Check derivation path *name* for a marker
    if not checked_features_successfully:
        log_info(f"Falling back to checking derivation path name for marker: '{CUDA_MARKER}'.")
        # Check if the marker string is present anywhere in the derivation path string
        if CUDA_MARKER in drv_path_str:
            log_info(f"Found '{CUDA_MARKER}' marker in path name."); needs_cuda_bindings = True
        else:
            log_info(f"Marker '{CUDA_MARKER}' not found in path name."); needs_cuda_bindings = False

    # --- Add sandbox paths if needed ---
    if needs_cuda_bindings:
        log_info("CUDA bindings determined necessary. Gathering required paths...")
        paths_to_bind = gather_potential_cuda_paths()

        valid_binds: List[Tuple[str, str]] = []
        if paths_to_bind:
            # Ensure paths actually exist before adding them to the final list
            # This prevents errors if, e.g., /dev/nvidia* exists but /dev/nvidia0 is gone
            for p in sorted(list(paths_to_bind), key=lambda x: x.as_posix()):
                 # Use is_symlink() check too, as we want to bind mount symlinks themselves
                 # The target will be resolved inside the sandbox if needed.
                 if p.exists() or p.is_symlink():
                     p_str = p.as_posix()
                     # Bind mount the path to itself (host path = guest path)
                     valid_binds.append((p_str, p_str))
                 else:
                     log_warning(f"Skipping non-existent path during final bind list creation: {p}")
        else:
             # This is a potential problem, GPU access will likely fail
             log_warning("Path gathering resulted in an empty set. No paths will be added to sandbox.")


        if not valid_binds:
             # Still exit cleanly, but warn loudly. The build might fail later.
             log_warning("No valid, existing paths found to bind mount for CUDA/GPU access.")
             sys.exit(0) # Exit without printing sandbox directives

        # Print the directives for Nix daemon
        log_info(f"Adding {len(valid_binds)} paths to sandbox:")
        # Use format required by pre-build-hook interface
        print("extra-sandbox-paths")
        for guest_path, host_path in valid_binds:
            log_info(f"  {guest_path} -> {host_path}")
            print(f"{guest_path}={host_path}")
        # Important: Print a trailing newline after the paths
        print()
        log_info("Sandbox paths printed successfully.")

    else:
        log_info("No CUDA bindings required for this derivation.")
        # Exit cleanly without printing anything; Nix proceeds without extra mounts
        sys.exit(0)
