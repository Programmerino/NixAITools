{
  description = "Nix Template";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs = inputs @ {flake-parts, ...}:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux"];
      perSystem = {
        system,
        ...
      }: let
        name = "NixTemplate";
        pkgs = import inputs.nixpkgs {
          inherit system;
        };
        saveEnv = pkgs.writeShellApplication {
          name = "saveEnv";
          text = ''
            sudo mkdir -p "/nix/var/nix/gcroots/per-user/$USER/saveEnv"
            sudo ln -sf "$(nix path-info --derivation .#all)" "/nix/var/nix/gcroots/per-user/$USER/saveEnv/${pkgs.lib.strings.escapeShellArg name}"
          '';
        };
        packages_.default = pkgs.hello;
      in rec {
        packages = packages_ // {
          all = pkgs.symlinkJoin {
            name = "all";
            paths = pkgs.lib.attrsets.attrValues packages_;
          };
        };
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [nil alejandra git];
          inputsFrom = [packages.default];
          shellHook = ''
              [[ -n $SAVE_ENV ]] && ( ${saveEnv}/bin/saveEnv >/dev/null 2>&1 & )
          '';
        };
        formatter = pkgs.alejandra;
      };
    };
}
