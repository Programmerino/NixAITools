# NixAITools

NixAITools is a collection of Nix utility functions for AI development and research, with a focus on efficient GPU resource management and reproducible AI environments.

## Features

- **CUDA VRAM Mutex**: Intelligent VRAM resource management for CUDA applications
- **cudaDerivation**: Enhanced Nix derivations for CUDA-enabled packages with VRAM management
- **fetchFromHF**: Easily fetch models and datasets from Hugging Face within Nix
- **NixOS Module**: System-level integration for better GPU support in Nix builds

## Installation

### As a Flake

Add NixAITools to your `flake.nix`:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
    nixaitools.url = "github:path/to/nixaitools"; # Replace with actual repo
  };

  outputs = { self, nixpkgs, ... }: {
    # Your flake outputs
  };
}
```

### For NixOS Users

Include the NixOS module in your configuration:

```nix
{ config, pkgs, ... }:

{
  imports = [
    inputs.nixaitools.nixosModules.default
  ];

  hardware.nvidia.nixaitools.enable = true;
}
```

## Usage

### CUDA Mutex

The `cuda_mutex` tool provides VRAM resource management for CUDA applications. It acts as a mutex to ensure that a specified amount of VRAM is available before launching an application.

```bash
# Reserve 5GB of VRAM on GPU 0 for your_cuda_app
cuda_mutex 5G -- your_cuda_app arg1 arg2

# Reserve 10GB of VRAM on GPU 1 with a 300s timeout
cuda_mutex -d 1 -t 300 -v 10G -- your_cuda_app --with-args
```

#### Options

- `size`: Amount of VRAM to reserve (e.g., 5G, 500M)
- `-d, --device`: CUDA device index (default: 0)
- `-t, --timeout`: Timeout in seconds (default: none)
- `-f, --force`: Force allocation even if there's not enough VRAM
- `-v, --verbose`: Enable verbose output
- `-q, --quiet`: Suppress all non-error output

### cudaDerivation

`cudaDerivation` enhances the standard Nix derivation with CUDA support and VRAM management:

```nix
{ pkgs, nixaitools, ... }:

let
  lib = nixaitools.lib.${pkgs.system};
in
  lib.cudaDerivation {
    name = "my-cuda-package";

    # Reserve 5GB of VRAM during build
    requiresVRAM = "5G";

    # Additional cuda_mutex options
    cudaMutexOpts = "-v -t 600";

    # Standard derivation attributes
    src = ./src;
    buildInputs = [ /* ... */ ];

    buildPhase = ''
      # Your build commands
    '';

    # ...
  }
```

### fetchFromHF

Fetch models or datasets directly from Hugging Face:

```nix
let
  nixaitools = inputs.nixaitools.lib system;
in {
  # Fetch a specific model
  myModel = nixaitools.fetchFromHF {
    repo = "facebook/opt-350m";
    files = [ "config.json" "pytorch_model.bin" ];
    rev = "...";  # Git commit
    hash = "sha256-..."; # Add hash for reproducibility
  };

  # Fetch a dataset
  myDataset = nixaitools.fetchFromHF {
    repo = "squad";
    repoType = "dataset";
    rev = "..."; # Git commit
    hash = "sha256-...";
  };

  # Fetch with authentication token
  myPrivateModel = nixaitools.fetchFromHF {
    repo = "org/private-model";
    token = "hf_..."; # Your Hugging Face token
    rev = "..."; # Git commit
    hash = "sha256-...";
  };
}
```

## NixOS Module

The NixOS module configures your system for better GPU support in Nix builds:

```nix
{ config, pkgs, ... }:

{
  hardware.nvidia.nixaitools = {
    enable = true;
  };
}
```

This module:
1. Makes CUDA devices accessible within Nix builds
2. Sets up necessary sandbox paths for the CUDA mutex
3. Installs a pre-build hook to properly expose NVIDIA drivers
