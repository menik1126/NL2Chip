module symbolic_parameter_behavior_tb;
    logic [2:0] concat_hi;
    logic [4:0] concat_lo;
    wire [7:0] concat_out;

    logic [17:0] slice_in;
    wire [16:0] slice_out;

    logic [64:0] zext_in;
    wire [65:0] zext_out;

    symbolicConcat #(.HI(3), .LO(5)) concat_dut (
        ._gen_hi(concat_hi),
        ._gen_lo(concat_lo),
        .out(concat_out)
    );

    symbolicSliceLow #(.W(17)) slice_dut (
        ._gen_x(slice_in),
        .out(slice_out)
    );

    symbolicZeroExtend #(.W(65)) zext_dut (
        ._gen_x(zext_in),
        .out(zext_out)
    );

    initial begin
        concat_hi = 3'b101;
        concat_lo = 5'b10011;
        slice_in = 18'b10_1010_1100_1111_0001;
        zext_in = {65{1'b1}};
        #1;

        if (concat_out !== {concat_hi, concat_lo})
            $fatal(1, "symbolic concat failed: got %b", concat_out);
        if (slice_out !== slice_in[16:0])
            $fatal(1, "symbolic slice failed: got %b", slice_out);
        if (zext_out !== {1'b0, zext_in})
            $fatal(1, "symbolic zero extension failed: got %b", zext_out);

        $display("P3_DERIVED_WIDTH_BEHAVIOR_PASS");
        $finish;
    end
endmodule
