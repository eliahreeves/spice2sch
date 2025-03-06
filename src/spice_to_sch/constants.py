from spice_to_sch.models import Point

io_origin = Point(-120, -40)
pmos_origin = Point(0, 0)
nmos_origin = pmos_origin + Point(0, 200)
spacing = 120
file_header = """v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
"""
