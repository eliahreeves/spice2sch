{
  description = "Dev Shell";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    ciel.url = "github:fossi-foundation/ciel";
  };
  outputs = inputs @ {
    flake-parts,
    ciel,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin"];
      perSystem = {pkgs, ...}: {
        devShells.default = pkgs.mkShell {
          packages = with pkgs;
            [
              uv
              xschem
              netgen-vlsi
              gnumake
              git
            ]
            ++ [
              ciel.packages.${pkgs.stdenv.hostPlatform.system}.ciel
            ];
        };
      };
    };
}
