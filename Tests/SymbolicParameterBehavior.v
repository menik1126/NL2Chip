module symbolic_parameter_behavior_tb;
    logic [2:0] concat_hi;
    logic [4:0] concat_lo;
    wire [7:0] concat_out;

    logic [17:0] slice_in;
    wire [16:0] slice_out;

    logic [64:0] zext_in;
    wire [65:0] zext_out;

    logic clk;
    logic rst;
    logic [2:0] reg3_in;
    wire [2:0] reg3_out;
    logic [16:0] reg17_in;
    wire [16:0] reg17_out;
    logic [64:0] reg65_in;
    wire [64:0] reg65_out;

    logic [1:0] mem2_write_addr;
    logic [2:0] mem2_write_data;
    logic mem2_write_enable;
    logic [1:0] mem2_read_addr;
    wire [2:0] mem2_read_data;

    logic [3:0] mem4_write_addr;
    logic [16:0] mem4_write_data;
    logic mem4_write_enable;
    logic [3:0] mem4_read_addr;
    wire [16:0] mem4_read_data;

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

    symbolicRegister #(.W(3)) reg3_dut (
        ._gen_x(reg3_in), .clk(clk), .rst(rst), .out(reg3_out)
    );

    symbolicRegister #(.W(17)) reg17_dut (
        ._gen_x(reg17_in), .clk(clk), .rst(rst), .out(reg17_out)
    );

    symbolicRegister #(.W(65)) reg65_dut (
        ._gen_x(reg65_in), .clk(clk), .rst(rst), .out(reg65_out)
    );

    symbolicMemory #(.ADDR_W(2), .DATA_W(3)) mem2_dut (
        ._gen_writeAddr(mem2_write_addr),
        ._gen_writeData(mem2_write_data),
        ._gen_writeEnable(mem2_write_enable),
        ._gen_readAddr(mem2_read_addr),
        .clk(clk), .rst(rst), .out(mem2_read_data)
    );

    symbolicMemory #(.ADDR_W(4), .DATA_W(17)) mem4_dut (
        ._gen_writeAddr(mem4_write_addr),
        ._gen_writeData(mem4_write_data),
        ._gen_writeEnable(mem4_write_enable),
        ._gen_readAddr(mem4_read_addr),
        .clk(clk), .rst(rst), .out(mem4_read_data)
    );

    initial begin
        concat_hi = 3'b101;
        concat_lo = 5'b10011;
        slice_in = 18'b10_1010_1100_1111_0001;
        zext_in = {65{1'b1}};
        clk = 1'b0;
        rst = 1'b0;
        reg3_in = 3'b110;
        reg17_in = 17'b1_0101_1001_1110_0011;
        reg65_in = {1'b1, 64'h0123_4567_89ab_cdef};
        mem2_write_addr = 2'd1;
        mem2_write_data = 3'b101;
        mem2_write_enable = 1'b0;
        mem2_read_addr = 2'd0;
        mem4_write_addr = 4'd3;
        mem4_write_data = 17'h1_2345;
        mem4_write_enable = 1'b0;
        mem4_read_addr = 4'd0;
        #1;

        if (concat_out !== {concat_hi, concat_lo})
            $fatal(1, "symbolic concat failed: got %b", concat_out);
        if (slice_out !== slice_in[16:0])
            $fatal(1, "symbolic slice failed: got %b", slice_out);
        if (zext_out !== {1'b0, zext_in})
            $fatal(1, "symbolic zero extension failed: got %b", zext_out);

        rst = 1'b1;
        #1;
        if (reg3_out !== 3'd1 || reg17_out !== 17'd1 || reg65_out !== 65'd1)
            $fatal(1, "symbolic register reset sizing failed");

        rst = 1'b0;
        #1;
        clk = 1'b1;
        #1;
        if (reg3_out !== reg3_in || reg17_out !== reg17_in || reg65_out !== reg65_in)
            $fatal(1, "symbolic register update failed");
        clk = 1'b0;

        mem2_write_enable = 1'b1;
        mem4_write_enable = 1'b1;
        #1;
        clk = 1'b1;
        #1;
        clk = 1'b0;

        mem2_write_enable = 1'b0;
        mem4_write_enable = 1'b0;
        mem2_read_addr = 2'd1;
        mem4_read_addr = 4'd3;
        #1;
        clk = 1'b1;
        #1;
        if (mem2_read_data !== 3'b101 || mem4_read_data !== 17'h1_2345)
            $fatal(1, "symbolic memory first-address read failed");
        clk = 1'b0;

        mem2_write_addr = 2'd2;
        mem2_write_data = 3'b011;
        mem2_write_enable = 1'b1;
        mem4_write_addr = 4'd12;
        mem4_write_data = 17'h0_abcd;
        mem4_write_enable = 1'b1;
        #1;
        clk = 1'b1;
        #1;
        clk = 1'b0;

        mem2_write_enable = 1'b0;
        mem4_write_enable = 1'b0;
        mem2_read_addr = 2'd2;
        mem4_read_addr = 4'd12;
        #1;
        clk = 1'b1;
        #1;
        if (mem2_read_data !== 3'b011 || mem4_read_data !== 17'h0_abcd)
            $fatal(1, "symbolic memory second-address read failed");

        $display("P3_SYMBOLIC_PARAMETER_BEHAVIOR_PASS");
        $finish;
    end
endmodule
