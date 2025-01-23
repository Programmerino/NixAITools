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
        pkgs = import inputs.nixpkgs {
          inherit system;
        };
      in rec {
        packages.default = pkgs.hello;
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [nil alejandra git] ++ packages.default.buildInputs ++ packages.default.nativeBuildInputs ++ packages.default.propagatedBuildInputs;
        };
        formatter = pkgs.alejandra;
      };
    };
}
