module prob005_notgate (_gen_input,
    out);
 input _gen_input;
 output out;

 wire net1;
 wire net2;

 sky130_fd_sc_hd__inv_1 _0_ (.A(net1),
    .Y(net2));
 sky130_fd_sc_hd__clkdlybuf4s50_1 input1 (.A(_gen_input),
    .X(net1));
 sky130_fd_sc_hd__clkdlybuf4s50_1 output2 (.A(net2),
    .X(out));
endmodule
