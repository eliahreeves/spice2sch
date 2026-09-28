PDK_ROOT  ?= $(HOME)/.ciel
PDK_HASH  := 0fe599b2afb6708d281543108caf8310912f54af
CELLS_DIR := .cache/sky130_fd_sc_hd
CELLS_REPO := https://github.com/fossi-foundation/skywater-pdk-libs-sky130_fd_sc_hd.git
CELL      ?= sky130_fd_sc_hd__inv_1

.PHONY: test test-full run clean

$(CELLS_DIR):
	mkdir -p $(dir $@)
	git clone --depth 1 $(CELLS_REPO) $@

test-full: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		uv run pytest tests -n auto -rs

test: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		uv run pytest tests -n auto -rs --cell-limit 16

run: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) \
		uv run spice2sch -i $(CELLS_DIR)/cells/*/$(CELL).spice -o /dev/null

clean:
	rm -rf .cache
