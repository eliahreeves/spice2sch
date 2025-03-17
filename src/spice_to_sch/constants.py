from spice_to_sch.models import Point

io_origin = Point(-120, -40)

inverter_origin = Point(120, 0)
transmission_gate_origin = Point(110, -400)
pmos_identical_origin = inverter_origin + Point(0, 200)
pmos_extra_origin = pmos_identical_origin + Point(0, 200)
nmos_identical_origin = pmos_extra_origin + Point(0, 200)
nmos_extra_origin = nmos_identical_origin + Point(0, 200)
parallel_origin = nmos_extra_origin + Point(0, 200)

spacing = 120
file_header = """v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
"""
