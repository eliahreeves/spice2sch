v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
C {ipin.sym} -120 -40 0 0 {name=p0 lab=A1}
C {ipin.sym} -120 -20 0 0 {name=p1 lab=A2}
C {ipin.sym} -120 0 0 0 {name=p2 lab=B1}
C {ipin.sym} -120 20 0 0 {name=p3 lab=VGND}
C {ipin.sym} -120 40 0 0 {name=p4 lab=VNB}
C {ipin.sym} -120 60 0 0 {name=p5 lab=VPB}
C {ipin.sym} -120 80 0 0 {name=p6 lab=VPWR}
C {opin.sym} -100 -60 0 0 {name=p7 lab=Y}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 20 30 0 0 {name=M0
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 40 30 2 0 {name=p8 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 40 0 2 0 {name=p9 sig_type=std_logic lab=VPWR}
C {lab_pin.sym} 40 60 2 0 {name=p10 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 0 30 0 0 {name=p11 sig_type=std_logic lab=A1}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 300 30 0 0 {name=M1
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 320 30 2 0 {name=p12 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 320 0 2 0 {name=p13 sig_type=std_logic lab=VPWR}
C {lab_pin.sym} 320 60 2 0 {name=p14 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 280 30 0 0 {name=p15 sig_type=std_logic lab=A2}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 20 210 0 0 {name=M2
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 40 210 2 0 {name=p16 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 40 180 2 0 {name=p17 sig_type=std_logic lab=VPWR}
C {lab_pin.sym} 40 240 2 0 {name=p18 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 0 210 0 0 {name=p19 sig_type=std_logic lab=A2}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 300 210 0 0 {name=M3
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 320 210 2 0 {name=p20 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 320 180 2 0 {name=p21 sig_type=std_logic lab=Y}
C {lab_pin.sym} 320 240 2 0 {name=p22 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 280 210 0 0 {name=p23 sig_type=std_logic lab=B1}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 20 390 0 0 {name=M4
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 40 390 2 0 {name=p24 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 40 360 2 0 {name=p25 sig_type=std_logic lab=VPWR}
C {lab_pin.sym} 40 420 2 0 {name=p26 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 0 390 0 0 {name=p27 sig_type=std_logic lab=A1}
C {sky130_fd_pr/pfet_01v8_hvt.sym} 300 390 0 0 {name=M5
W=1e+06u
L=150000u
model=pfet_01v8_hvt
spiceprefix=X
}
C {lab_pin.sym} 320 390 2 0 {name=p28 sig_type=std_logic lab=VPB}
C {lab_pin.sym} 320 360 2 0 {name=p29 sig_type=std_logic lab=a_27_297#}
C {lab_pin.sym} 320 420 2 0 {name=p30 sig_type=std_logic lab=Y}
C {lab_pin.sym} 280 390 0 0 {name=p31 sig_type=std_logic lab=B1}
C {sky130_fd_pr/nfet_01v8.sym} 20 930 0 0 {name=M6
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 40 930 2 0 {name=p32 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 40 900 2 0 {name=p33 sig_type=std_logic lab=a_114_47#}
C {lab_pin.sym} 40 960 2 0 {name=p34 sig_type=std_logic lab=VGND}
C {lab_pin.sym} 0 930 0 0 {name=p35 sig_type=std_logic lab=A2}
C {sky130_fd_pr/nfet_01v8.sym} 300 930 0 0 {name=M7
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 320 930 2 0 {name=p36 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 320 900 2 0 {name=p37 sig_type=std_logic lab=Y}
C {lab_pin.sym} 320 960 2 0 {name=p38 sig_type=std_logic lab=VGND}
C {lab_pin.sym} 280 930 0 0 {name=p39 sig_type=std_logic lab=B1}
C {sky130_fd_pr/nfet_01v8.sym} 20 1110 0 0 {name=M8
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 40 1110 2 0 {name=p40 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 40 1080 2 0 {name=p41 sig_type=std_logic lab=Y}
C {lab_pin.sym} 40 1140 2 0 {name=p42 sig_type=std_logic lab=VGND}
C {lab_pin.sym} 0 1110 0 0 {name=p43 sig_type=std_logic lab=B1}
C {sky130_fd_pr/nfet_01v8.sym} 300 1110 0 0 {name=M9
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 320 1110 2 0 {name=p44 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 320 1080 2 0 {name=p45 sig_type=std_logic lab=a_285_47#}
C {lab_pin.sym} 320 1140 2 0 {name=p46 sig_type=std_logic lab=VGND}
C {lab_pin.sym} 280 1110 0 0 {name=p47 sig_type=std_logic lab=A2}
C {sky130_fd_pr/nfet_01v8.sym} 20 1290 0 0 {name=M10
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 40 1290 2 0 {name=p48 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 40 1260 2 0 {name=p49 sig_type=std_logic lab=a_114_47#}
C {lab_pin.sym} 40 1320 2 0 {name=p50 sig_type=std_logic lab=Y}
C {lab_pin.sym} 0 1290 0 0 {name=p51 sig_type=std_logic lab=A1}
C {sky130_fd_pr/nfet_01v8.sym} 300 1290 0 0 {name=M11
W=650000u
L=150000u
model=nfet_01v8
spiceprefix=X
}
C {lab_pin.sym} 320 1290 2 0 {name=p52 sig_type=std_logic lab=VNB}
C {lab_pin.sym} 320 1260 2 0 {name=p53 sig_type=std_logic lab=Y}
C {lab_pin.sym} 320 1320 2 0 {name=p54 sig_type=std_logic lab=a_285_47#}
C {lab_pin.sym} 280 1290 0 0 {name=p55 sig_type=std_logic lab=A1}
