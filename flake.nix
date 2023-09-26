{
  description = "Nix Template";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs";
    nil.url = "github:oxalica/nil";
    nil.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = inputs @ {
    flake-parts,
    nil,
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
      }: {
        packages.default = pkgs.hello;
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [nil.packages."${system}".default alejandra];
        };
        formatter = pkgs.alejandra;
      };
      flake = {
        templates.default = {
          path = ./template;
          description = "A template for Nix projects with good design and VSCode integration";
          welcomeText = "";
        };
      };
    };
}
