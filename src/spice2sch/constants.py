from spice2sch.models import Point

io_origin = Point(-120, -40)

primitive_origin = Point(120, 0)

spacing = 120

# Gap between placeable extents, which already include their net labels.
placement_gap = 40
file_header = """v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
"""
