# spice-to-sch

A CLI to convert SkyWater SKY130 spice files into xschem .sch files. Designed for [sifferman/sky130_schematics](https://github.com/sifferman/sky130_schematics). Available from [PyPi](https://pypi.org/project/spice-to-sch/).

## Use without installing

### For uv users
```bash
uvx spice-to-sch -h
```

## Installation

### For uv users (recommended)

```bash
uv tool install spice_to_sch
```

### For pip users

```bash
pip install spice_to_sch
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

## Example

1. Generate a sch file. The following command uses uvx to use the package without downloading, and pipes a spice netlist from the clipboard to the tool.

```bash
wl-paste | uvx spice-to-sch -o sky130_fd_sc_hd__xor3_4.sch
```

2. Open the generated .sch file and manually arrange components.
   After running tool:
   ![pre](readme_images/xor3_4_pre.png)
   After organizing gates:
   ![post](readme_images/xor3_4_post.png)

## Limitations

- Currently this program will assume all components are transistors. Using this on a .spice file with other components will not work.
- Although schematics will pass a Layout Versus Schematic (LVS) check, all components must be manually rearranged.

## Running from source with uv

Clone the repo

```bash
git clone git@github.com:eliahreeves/spice-to-sch.git
cd spice-to-sh
```

Build and run

```bash
uv run spice-to-sch
```

> [!NOTE]
> You may need to remove existing installations using `uv tool uninstall spice-to-sch` or similar in order to avoid namespace confilcts.
