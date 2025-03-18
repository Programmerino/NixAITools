{
  description = "Nix AI utility functions";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
  };

  outputs = inputs @ {flake-parts, self, ...}:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux" "aarch64-darwin" "x86_64-darwin"];
      flake = {
        lib = system: let
        pkgs = import inputs.nixpkgs {
          inherit system;
          config.allowUnfree = true;
          config.cudaSupport = true;
        };
        in {
        fetchFromHF = {repo, repoType ? "model", files ? null, token ? null, hash ? "", rev}: pkgs.stdenv.mkDerivation {
          name = "${repo}";
          nativeBuildInputs = with pkgs.python3Packages; [huggingface-hub];
          phases = ["installPhase"];
          outputHashAlgo = "sha256";
          outputHashMode = "recursive";
          outputHash = hash;
          installPhase = let
            filesString = if files != null then builtins.concatStringsSep " " files else "";
          in ''
            export HOME="$(mktemp -d)"
            ${if token != null then ''export HF_TOKEN="${token}"'' else ""}
            huggingface-cli download "${repo}" ${filesString} --repo-type "${repoType}" --quiet --local-dir "$out" --revision "${rev}"
          '';
        };
        cudaDerivation = {requiredSystemFeatures ? [], nativeBuildInputs ? [], buildPhase ? "", requiresVRAM ? null, cudaMutexOpts ? " ", ...}@args: pkgs.stdenv.mkDerivation ({
          requiredSystemFeatures = requiredSystemFeatures ++ ["expose-cuda"];
          nativeBuildInputs = nativeBuildInputs ++ (with pkgs; [cudaPackages.cuda_cudart cudaPackages.cuda_cccl cudaPackages.cuda_nvcc]);
          buildPhase = if requiresVRAM != null then
          let
            origPhase = pkgs.writeShellScript "buildPhase" buildPhase;
          in ''
            ${self.packages."${system}".cuda_mutex}/bin/cuda_mutex ${requiresVRAM} ${cudaMutexOpts} -- ${origPhase}
          '' else buildPhase;
        } // pkgs.lib.removeAttrs args ["requiredSystemFeatures" "nativeBuildInputs" "buildPhase" "requiresVRAM" "cudaMutexOpts"]);
        };

        nixosModules.default = {pkgs, config, ...}: {
          options = {
            hardware.nvidia.nixaitools = {
              enable = pkgs.lib.mkOption {
                type = pkgs.types.bool;
                default = false;
                description = "Make modifications necessary for Nix AI tools to work";
              };
            };
          };
          config =
            if config.hardware.nvidia.nixaitools
            then {
              nix.settings.extra-sandbox-paths = [
                "/tmp/cuda_mutex.lock"
                "/tmp/cuda_mutex.json"
              ];

              # https://github.com/ogoid/nixos-expose-cuda/tree/master
              nix.settings.system-features = [ "expose-cuda" ];
              nix.settings.pre-build-hook = pkgs.writers.writePython3 "nix-pre-build.py" { }
                (builtins.readFile ./nix-pre-build-hook.py);
            }
            else {};
        };
      };
      perSystem = {system, ...}: let
        pkgs = import inputs.nixpkgs {
          inherit system;
        };
        cuda_mutex = pkgs.stdenv.mkDerivation {
            name = "cuda_mutex";
            propagatedBuildInputs = [
            (pkgs.python3.withPackages (pythonPackages:
                with pythonPackages; [
                    pynvml
                ]))
            ];
            dontUnpack = true;
            installPhase = "install -Dm755 ${./cuda_mutex} $out/bin/cuda_mutex";
        };
      in {
        packages.cuda_mutex = cuda_mutex;
      };
    };
}
