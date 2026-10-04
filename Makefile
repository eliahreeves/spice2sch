PDK_ROOT  ?= $(HOME)/.ciel
PDK_HASH  := 0fe599b2afb6708d281543108caf8310912f54af
GF180_PDK_HASH := 1e3554706ddbb2529193bdabbc7128296f3cb44a
CELLS_DIR := .cache/sky130_fd_sc_hd
CELLS_REPO := https://github.com/fossi-foundation/skywater-pdk-libs-sky130_fd_sc_hd.git
GF180_CELLS_DIR := .cache/gf180mcu_fd_sc_mcu7t5v0
GF180_CELLS_REPO := https://github.com/fossi-foundation/globalfoundries-pdk-libs-gf180mcu_fd_sc_mcu7t5v0.git
CELL      ?= sky130_fd_sc_hd__inv_1

.PHONY: test test-full run gf180 clean

$(CELLS_DIR):
	mkdir -p $(dir $@)
	git clone --depth 1 $(CELLS_REPO) $@

$(GF180_CELLS_DIR):
	mkdir -p $(dir $@)
	git clone --depth 1 $(GF180_CELLS_REPO) $@

test-full: $(CELLS_DIR) $(GF180_CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	ciel enable --pdk-family gf180mcu $(GF180_PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		GF180_CELLS=$(abspath $(GF180_CELLS_DIR)) \
		uv run pytest tests -n auto -rs

test: $(CELLS_DIR) $(GF180_CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	ciel enable --pdk-family gf180mcu $(GF180_PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) SKY130_CELLS=$(abspath $(CELLS_DIR)) \
		GF180_CELLS=$(abspath $(GF180_CELLS_DIR)) \
		uv run pytest tests -n auto -rs --cell-limit 16

run: $(CELLS_DIR)
	ciel enable --pdk-family sky130 $(PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) \
		uv run spice2sch -i $(CELLS_DIR)/cells/*/$(CELL).spice -o /dev/null

gf180: CELL = gf180mcu_fd_sc_mcu7t5v0__inv_1
gf180: $(GF180_CELLS_DIR)
	ciel enable --pdk-family gf180mcu $(GF180_PDK_HASH)
	PDK_ROOT=$(PDK_ROOT) \
		uv run spice2sch -i $(GF180_CELLS_DIR)/cells/*/$(CELL).cdl -o /dev/null

clean:
	rm -rf .cache
