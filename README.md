# spice-to-sch
A CLI to convert SkyWater SKY130 spice files into xschem .sch files. Designed for [sifferman/sky130_schematics](https://github.com/sifferman/sky130_schematics).
## Installation
Download the latest .whl file from [releases](https://github.com/eliahreeves/spice-to-sch/releases/latest).
### For uv users
```bash
uv tool install spice_to_sch-0.1.0-py3-none-any.whl
```
### For pip users (untested)
```bash
pip install spice_to_sch-0.1.0-py3-none-any.whl
```
## Usage
> [!CAUTION]
> The output file will be overwritten without warning.

Specify and input .spice and and output .sch file.
```bash
spice-to-sch -i file.spice -o file.sch
```
Input and output will default to stdin and stdout making this equivalent to the above command:
```bash
cat file.spice | spice-to-sch > file.sch
```
## Limitations
* Currently this program will assume all components are transistors. Using this on a .spice file with other components will not work.
* Although schematics will pass a Layout Versus Schematic (LVS) check, all components will be in a simple grid and must be manually rearranged.
