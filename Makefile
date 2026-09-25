PDK_ROOT  ?= $(HOME)/.ciel
PDK_HASH  := 0fe599b2afb6708d281543108caf8310912f54af
CELLS_DIR := .cache/sky130_fd_sc_hd
CELLS_REPO := https://github.com/fossi-foundation/skywater-pdk-libs-sky130_fd_sc_hd.git

.PHONY: test test-full clean

$(CELLS_DIR):
	mkdir -p $(dir $@)
	git clone --depth 1 $(CELLS_REPO) $@

test-full: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		uv run pytest tests -rs

test: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		uv run pytest tests -rs --cell-limit 10

clean:
	rm -rf .cache
