{
  description = "Nix AI utility functions";

  nixConfig = {
    trusted-public-keys = ["nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs=" "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="];
    substituters = ["https://cache.nixos.org" "https://nix-community.cachix.org"];
  };

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/16762245d811fdd74b417cc922223dc8eb741e8b";
    dream2nix.url = "github:nix-community/dream2nix";
    dream2nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = inputs @ {
    flake-parts,
    self,
    dream2nix,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux"];
      flake = {
        lib = system: let
          pkgs = import inputs.nixpkgs {
            inherit system;
            config.allowUnfree = true;
            config.cudaSupport = true;
          };
          myPkgs = self.packages."${system}";
        in rec {
          fetchFromHF = {
            repo,
            repoType ? "model",
            files ? null,
            token ? null,
            hash ? "",
            rev,
          }:
            pkgs.stdenv.mkDerivation {
              name = "${repo}";
              nativeBuildInputs = with pkgs.python3Packages; [huggingface-hub];
              phases = ["installPhase"];
              outputHashAlgo = "sha256";
              outputHashMode = "recursive";
              outputHash = hash;

              # Since datasets may contain nix store paths, we need to disable the references check.
              __structuredAttrs = true;
              unsafeDiscardReferences.out = true;

              installPhase = let
                filesString =
                  if files != null
                  then builtins.concatStringsSep " " files
                  else "";
              in ''
                export HOME="$(mktemp -d)"
                ${
                  if token != null
                  then ''export HF_TOKEN="${token}"''
                  else ""
                }
                huggingface-cli download "${repo}" ${filesString} --repo-type "${repoType}" --quiet --local-dir "$out" --revision "${rev}"
                rm -rf $out/.cache || true
              '';
            };
          cudaDerivation = {
            name,
            requiredSystemFeatures ? [],
            nativeBuildInputs ? [],
            buildPhase ? "",
            requiresVRAM ? null,
            cudaMutexOpts ? " ",
            hash ? null,
            ...
          } @ args:
            pkgs.stdenv.mkDerivation ({
                name = "${args.name}-wants-cuda";
                requiredSystemFeatures = requiredSystemFeatures ++ ["cuda"];
                nativeBuildInputs = nativeBuildInputs ++ (with pkgs; [cudaPackages.cuda_cudart cudaPackages.cuda_cccl cudaPackages.cuda_nvcc]);
                buildPhase =
                  if requiresVRAM != null
                  then let
                    origPhase = pkgs.writeShellScript "buildPhase" buildPhase;
                  in ''
                    ${self.packages."${system}".cuda_mutex}/bin/cuda_mutex ${requiresVRAM} ${cudaMutexOpts} -- ${origPhase}
                  ''
                  else buildPhase;
              }
              // (if hash != null then {
                __structuredAttrs = true;
                unsafeDiscardReferences.out = true;
                outputHashAlgo = "sha256";
                outputHashMode = "recursive";
                outputHash = hash;
              } else {})
              // pkgs.lib.removeAttrs args ["name" "requiredSystemFeatures" "nativeBuildInputs" "buildPhase" "requiresVRAM" "hash" "cudaMutexOpts"]);

          axolotl = rec {
            mkPreprocessedDataset = {
              name,
              trainParams,
            } @ args: let
              trainParams' =
                args.trainParams
                // {
                  dataset_prepared_path = "REPLACE_ME_PREPARED";
                };
              train_yml = (pkgs.formats.yaml {}).generate "axolotl_train.yml" trainParams';
            in
              pkgs.stdenvNoCC.mkDerivation {
                name = "${name}-axolotl-dataset";
                nativeBuildInputs = [myPkgs.axolotl];
                phases = ["buildPhase"];
                buildPhase = ''
                  mkdir -p $out
                  cp ${train_yml} ./axolotl_train.yml
                  sed -i "s|REPLACE_ME_PREPARED|$out|g" ./axolotl_train.yml
                  axolotl preprocess ./axolotl_train.yml
                '';
              };
            runSweep = {
              name,
              preprocess ? false,
              trainParams,
              sweepParams,
              ...
            } @ args: let
              trainParams' = if preprocess
                then args.trainParams // {
                  dataset_prepared_path = let
                    dataset = mkPreprocessedDataset {
                      inherit name;
                      inherit trainParams;
                    };
                  in "${dataset}";
                }
                else args.trainParams;
              train_yml = (pkgs.formats.yaml {}).generate "axolotl_train.yml" trainParams;
              sweep_yml = (pkgs.formats.yaml {}).generate "axolotl_sweep.yml" sweepParams;
              args' = builtins.removeAttrs args ["trainParams" "sweepParams" "preprocess"];
            in
              cudaDerivation (args'
                // {
                  name = "${name}-axolotl-sweep";
                  nativeBuildInputs = [myPkgs.axolotl];
                  buildPhase = ''
                    mkdir -p $out
                    cp ${train_yml} ./axolotl_train.yml
                    cp ${sweep_yml} ./axolotl_sweep.yml

                    # Not sure what the output is
                    axolotl train ./axolotl_train.yml --sweep ./axolotl_sweep.yml
                  '';
                });

            train = {
              name,
              preprocess ? false,
              trainParams,
              ...
            } @ args: let
              trainParams' =
                args.trainParams
                // {
                  output_dir = "REPLACE_ME_OUT";
                };
              trainParams'' = if preprocess
                then trainParams' // {
                  dataset_prepared_path = let
                    dataset = mkPreprocessedDataset {
                      inherit name;
                      inherit trainParams;
                    };
                  in "${dataset}";
                }
                else trainParams';
              train_yml = (pkgs.formats.yaml {}).generate "axolotl_train.yml" trainParams'';
              args' = builtins.removeAttrs args ["preprocess ""trainParams"];
            in
              cudaDerivation (args'
                // {
                  name = "${name}-axolotl-train";
                  nativeBuildInputs = [myPkgs.axolotl];
                  buildPhase = ''
                    mkdir -p $out
                    cp ${train_yml} ./axolotl_train.yml
                    sed -i "s|REPLACE_ME_OUT|$out|g" ./axolotl_train.yml
                    axolotl train ./axolotl_train.yml
                  '';
                });
          };
        };

        nixosModules.default = {
          pkgs,
          config,
          lib,
          ...
        }:
          with lib; {
            options = {
              hardware.nvidia.nixaitools = {
                enable = mkEnableOption "Make modifications necessary for Nix AI tools to work";
              };
            };
            config = mkIf config.hardware.nvidia.nixaitools.enable {
              nix.settings.extra-sandbox-paths = [
                "/tmp/cuda_mutex.lock"
                "/tmp/cuda_mutex.json"
              ];

              systemd.tmpfiles.rules = [
                "f /tmp/cuda_mutex.lock 0666 root root - -"
                "f /tmp/cuda_mutex.json 0666 root root - -"
              ];

              # Based on https://github.com/ogoid/nixos-expose-cuda/tree/master
              nix.settings.system-features = ["cuda"];
              nix.settings.pre-build-hook = myPkgs.axolotl;
            };
          };
      };
      perSystem = {system, ...}: let
        pkgs = import inputs.nixpkgs {
          inherit system;
          config.allowUnfree = true;
          config.cudaSupport = true;
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
        axolotl = dream2nix.lib.evalModules {
          packageSets.nixpkgs = pkgs;
          modules = [
            ({
              config,
              lib,
              dream2nix,
              ...
            }: {
              imports = [
                dream2nix.modules.dream2nix.pip
              ];

              deps = {nixpkgs, ...}: {
                python = nixpkgs.python311;
              };

              mkDerivation.buildInputs = [
                (config.deps.python.pkgs.callPackage ./flash-attn.nix {})
              ];

              name = "axolotl";
              version = "0.8.1";

              pip = {
                # We have to build flash-attn separately
                requirementsList = ["${config.name}[apollo]==${config.version}"];
                overrides.numba = {
                  mkDerivation.buildInputs = with pkgs; [tbb_2022_0];
                };
                overrides.torchao = {
                  mkDerivation.buildInputs = [config.pip.drvs.torch.public];
                  mkDerivation.preInstall = ''
                    addAutoPatchelfSearchPath "${config.pip.drvs.torch.public}/lib/python3.11/site-packages/torch/lib"
                  '';
                };
                overrides.axolotl-contribs-lgpl = {
                  buildPythonPackage.pyproject = true;
                  mkDerivation.buildInputs = [config.deps.python.pkgs.setuptools];
                };
                overrides.axolotl-contribs-mit = {
                  buildPythonPackage.pyproject = true;
                  mkDerivation.buildInputs = [config.deps.python.pkgs.setuptools];
                };
                overrides.bitsandbytes = {
                  env.autoPatchelfIgnoreMissingDeps = true;
                };
              };
            })
            {
              paths.projectRoot = ./.;
              paths.projectRootFile = "flake.nix";
              paths.package = ./.;
            }
          ];
        };
      in {
        packages.cuda_mutex = cuda_mutex;
        packages.axolotl = axolotl;
        packages.nix-pre-build = pkgs.writers.writePython3 "nix-pre-build.py" {doCheck = false;} (builtins.readFile ./nix-pre-build-hook.py);
      };
    };
}
