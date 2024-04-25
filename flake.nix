{
  description = "Nix Template";

  nixConfig = {
    extra-substituters = [
      "https://programmerino.cachix.org"
    ];
    extra-trusted-public-keys = [
      "programmerino.cachix.org-1:v8UWI2QVhEnoU71CDRNS/K1CcW3yzrQxJc604UiijjA="
    ];
  };

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
  };

  outputs = inputs @ {
    flake-parts,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux"];
      perSystem = {
        config,
        self',
        inputs',
        pkgs,
        system,
        ...
      }: rec {
        packages.default = pkgs.hello;
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [nil alejandra git gitg] ++ packages.default.buildInputs ++ packages.default.nativeBuildInputs ++ packages.default.propagatedBuildInputs;
        };
        formatter = pkgs.alejandra;
      };
    };
}
