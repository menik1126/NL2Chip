module symbolic_parameter_behavior_tb;
    logic [2:0] concat_hi;
    logic [4:0] concat_lo;
    wire [7:0] concat_out;

    logic [17:0] slice_in;
    wire [16:0] slice_out;

    logic [64:0] zext_in;
    wire [65:0] zext_out;
    logic [64:0] cast_extend_in;
    wire [65:0] cast_extend_out;
    logic [65:0] cast_trunc_in;
    wire [64:0] cast_trunc_out;
    logic [16:0] modulo_lhs;
    logic [16:0] modulo_rhs;
    wire [16:0] modulo_out;
    logic [2:0] repeat_in;
    wire [11:0] repeat_out;
    logic [4:0] repeat_alt_in;
    wire [34:0] repeat_alt_out;
    wire [11:0] iota_out;
    wire [14:0] iota_alt_out;
    logic [11:0] map_chunks_in;
    wire [11:0] map_chunks_out;
    logic [11:0] map_chunks_indexed_in;
    wire [11:0] map_chunks_indexed_out;
    logic [11:0] map_chunks_narrow_in;
    wire [5:0] map_chunks_narrow_out;
    logic [19:0] map_chunks_narrow_alt_in;
    wire [9:0] map_chunks_narrow_alt_out;

    logic clk;
    logic rst;
    logic [2:0] reg3_in;
    wire [2:0] reg3_out;
    logic [16:0] reg17_in;
    wire [16:0] reg17_out;
    logic [64:0] reg65_in;
    wire [64:0] reg65_out;
    logic [2:0] derived8_in;
    wire [2:0] derived8_out;
    logic [3:0] derived12_in;
    wire [3:0] derived12_out;
    logic [3:0] derived16_in;
    wire [3:0] derived16_out;
    logic [2:0] alias8_in;
    wire [2:0] alias8_out;
    logic [3:0] alias12_in;
    wire [3:0] alias12_out;
    logic [3:0] alias16_in;
    wire [3:0] alias16_out;
    logic [2:0] loop3_in;
    wire [4:0] loop3_out;
    logic [16:0] loop17_in;
    wire [18:0] loop17_out;
    logic [2:0] pair3_in;
    wire [5:0] pair3_out;
    logic [16:0] pair17_in;
    wire [33:0] pair17_out;
    logic [2:0] wide_tuple3_in;
    wire [18:0] wide_tuple3_out;
    logic [4:0] wide_tuple5_in;
    wire [28:0] wide_tuple5_out;
    logic [3:0] depth8_in;
    wire depth8_out;
    logic [3:0] depth12_in;
    wire depth12_out;
    logic [4:0] depth16_in;
    wire depth16_out;




    logic [1:0] mem2_write_addr;
    logic [2:0] mem2_write_data;
    logic mem2_write_enable;
    logic [1:0] mem2_read_addr;
    wire [2:0] mem2_read_data;
    wire [2:0] mem2_combo_read_data;

    logic [3:0] mem4_write_addr;
    logic [16:0] mem4_write_data;
    logic mem4_write_enable;
    logic [3:0] mem4_read_addr;
    wire [16:0] mem4_read_data;
    wire [16:0] mem4_combo_read_data;

    logic [2:0] hier3_lhs;
    logic [2:0] hier3_rhs;
    wire [2:0] hier3_out;
    logic [16:0] hier17_lhs;
    logic [16:0] hier17_rhs;
    wire [16:0] hier17_out;
    logic [64:0] hier65_lhs;
    logic [64:0] hier65_rhs;
    wire [64:0] hier65_out;
    logic [2:0] letmask3_in;
    wire [2:0] letmask3_out;
    logic [16:0] letmask17_in;
    wire [16:0] letmask17_out;
    logic [2:0] value_expr3_in;
    wire [2:0] value_expr3_out;
    logic [4:0] value_expr5_in;
    wire [4:0] value_expr5_out;
    logic [2:0] bundle_all3_in;
    wire [6:0] bundle_all3_out;
    logic [4:0] bundle_all5_in;
    wire [10:0] bundle_all5_out;

    logic [2:0] generate3_in;
    wire [2:0] generate3_out;
    logic [16:0] generate17_in;
    wire [16:0] generate17_out;
    logic [64:0] generate65_in;
    wire [64:0] generate65_out;
    logic [2:0] popcount3_in;
    wire [1:0] popcount3_out;
    logic [16:0] popcount17_in;
    wire [4:0] popcount17_out;
    logic [2:0] uge3_lhs;
    logic [2:0] uge3_rhs;
    wire uge3_out;
    logic [16:0] uge17_lhs;
    logic [16:0] uge17_rhs;
    wire uge17_out;
    logic [2:0] reverse3_in;
    wire [2:0] reverse3_out;
    logic [16:0] reverse17_in;
    wire [16:0] reverse17_out;
    logic [15:0] reverse_blocks_in;
    wire [15:0] reverse_blocks2_out;
    wire [15:0] reverse_blocks4_out;
    wire [15:0] reverse_blocks8_out;

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

    symbolicCastExtend #(.W(65)) cast_extend_dut (
        ._gen_x(cast_extend_in),
        .out(cast_extend_out)
    );

    symbolicCastTrunc #(.W(65)) cast_trunc_dut (
        ._gen_x(cast_trunc_in),
        .out(cast_trunc_out)
    );

    symbolicModulo #(.W(17)) modulo_dut (
        ._gen_lhs(modulo_lhs),
        ._gen_rhs(modulo_rhs),
        .out(modulo_out)
    );

    symbolicRepeatVector #(.W(3), .N(4)) repeat_dut (
        ._gen_x(repeat_in),
        .out(repeat_out)
    );

    symbolicRepeatVector #(.W(5), .N(7)) repeat_alt_dut (
        ._gen_x(repeat_alt_in),
        .out(repeat_alt_out)
    );

    symbolicIotaVector1 #(.W(4), .N(3)) iota_dut (
        .out(iota_out)
    );

    symbolicIotaVector1 #(.W(3), .N(5)) iota_alt_dut (
        .out(iota_alt_out)
    );

    symbolicMapChunks #(.W(3), .N(4)) map_chunks_dut (
        ._gen_x(map_chunks_in),
        .out(map_chunks_out)
    );

    symbolicMapChunksWithIndex #(.W(3), .N(4)) map_chunks_indexed_dut (
        ._gen_x(map_chunks_indexed_in),
        .out(map_chunks_indexed_out)
    );

    symbolicMapChunksNarrow #(.N(3)) map_chunks_narrow_dut (
        ._gen_x(map_chunks_narrow_in),
        .out(map_chunks_narrow_out)
    );

    symbolicMapChunksNarrow #(.N(5)) map_chunks_narrow_alt_dut (
        ._gen_x(map_chunks_narrow_alt_in),
        .out(map_chunks_narrow_alt_out)
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

    symbolicDerivedLoop #(.DEPTH(8)) derived8_dut (
        ._gen_x(derived8_in), .clk(clk), .rst(rst), .out(derived8_out)
    );

    symbolicDerivedLoop #(.DEPTH(12)) derived12_dut (
        ._gen_x(derived12_in), .clk(clk), .rst(rst), .out(derived12_out)
    );

    symbolicDerivedLoop #(.DEPTH(16)) derived16_dut (
        ._gen_x(derived16_in), .clk(clk), .rst(rst), .out(derived16_out)
    );

    symbolicDerivedAlias #(.DEPTH(8)) alias8_dut (
        ._gen_x(alias8_in), .clk(clk), .rst(rst), .out(alias8_out)
    );

    symbolicDerivedAlias #(.DEPTH(12)) alias12_dut (
        ._gen_x(alias12_in), .clk(clk), .rst(rst), .out(alias12_out)
    );

    symbolicDerivedAlias #(.DEPTH(16)) alias16_dut (
        ._gen_x(alias16_in), .clk(clk), .rst(rst), .out(alias16_out)
    );

    symbolicLoopBundle #(.W(3)) loop3_dut (
        ._gen_x(loop3_in), .clk(clk), .rst(rst), .out(loop3_out)
    );

    symbolicLoopBundle #(.W(17)) loop17_dut (
        ._gen_x(loop17_in), .clk(clk), .rst(rst), .out(loop17_out)
    );

    symbolicPairLoop #(.W(3)) pair3_dut (
        ._gen_x(pair3_in), .clk(clk), .rst(rst), .out(pair3_out)
    );

    symbolicPairLoop #(.W(17)) pair17_dut (
        ._gen_x(pair17_in), .clk(clk), .rst(rst), .out(pair17_out)
    );

    symbolicWideTupleLoop #(.W(3)) wide_tuple3_dut (
        ._gen_x(wide_tuple3_in), .clk(clk), .rst(rst), .out(wide_tuple3_out)
    );

    symbolicWideTupleLoop #(.W(5)) wide_tuple5_dut (
        ._gen_x(wide_tuple5_in), .clk(clk), .rst(rst), .out(wide_tuple5_out)
    );
    symbolicDepthCompare #(.DEPTH(8)) depth8_dut (
        ._gen_x(depth8_in), .clk(clk), .rst(rst), .out(depth8_out)
    );

    symbolicDepthCompare #(.DEPTH(12)) depth12_dut (
        ._gen_x(depth12_in), .clk(clk), .rst(rst), .out(depth12_out)
    );

    symbolicDepthCompare #(.DEPTH(16)) depth16_dut (
        ._gen_x(depth16_in), .clk(clk), .rst(rst), .out(depth16_out)
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

    symbolicComboMemory #(.ADDR_W(2), .DATA_W(3)) mem2_combo_dut (
        ._gen_writeAddr(mem2_write_addr),
        ._gen_writeData(mem2_write_data),
        ._gen_writeEnable(mem2_write_enable),
        ._gen_readAddr(mem2_read_addr),
        .clk(clk), .rst(rst), .out(mem2_combo_read_data)
    );

    symbolicComboMemory #(.ADDR_W(4), .DATA_W(17)) mem4_combo_dut (
        ._gen_writeAddr(mem4_write_addr),
        ._gen_writeData(mem4_write_data),
        ._gen_writeEnable(mem4_write_enable),
        ._gen_readAddr(mem4_read_addr),
        .clk(clk), .rst(rst), .out(mem4_combo_read_data)
    );

    symbolicXorHierarchy #(.W(3)) hier3_dut (
        ._gen_lhs(hier3_lhs), ._gen_rhs(hier3_rhs), .out(hier3_out)
    );

    symbolicXorHierarchy #(.W(17)) hier17_dut (
        ._gen_lhs(hier17_lhs), ._gen_rhs(hier17_rhs), .out(hier17_out)
    );

    symbolicXorHierarchy #(.W(65)) hier65_dut (
        ._gen_lhs(hier65_lhs), ._gen_rhs(hier65_rhs), .out(hier65_out)
    );

    symbolicLetMaskXor #(.W(3)) letmask3_dut (._gen_x(letmask3_in), .out(letmask3_out));
    symbolicLetMaskXor #(.W(17)) letmask17_dut (._gen_x(letmask17_in), .out(letmask17_out));

    symbolicGenerateNot #(.W(3)) generate3_dut (
        ._gen_x(generate3_in), .out(generate3_out)
    );

    symbolicGenerateNot #(.W(17)) generate17_dut (
        ._gen_x(generate17_in), .out(generate17_out)
    );

    symbolicGenerateNot #(.W(65)) generate65_dut (
        ._gen_x(generate65_in), .out(generate65_out)
    );

    symbolicPopCount #(.W(3)) popcount3_dut (
        ._gen_x(popcount3_in), .out(popcount3_out)
    );

    symbolicPopCount #(.W(17)) popcount17_dut (
        ._gen_x(popcount17_in), .out(popcount17_out)
    );
    symbolicUnsignedGE #(.W(3)) uge3_dut (
        ._gen_lhs(uge3_lhs), ._gen_rhs(uge3_rhs), .out(uge3_out)
    );

    symbolicUnsignedGE #(.W(17)) uge17_dut (
        ._gen_lhs(uge17_lhs), ._gen_rhs(uge17_rhs), .out(uge17_out)
    );
    symbolicReverseBits #(.W(3)) reverse3_dut (
        ._gen_x(reverse3_in), .out(reverse3_out)
    );

    symbolicReverseBits #(.W(17)) reverse17_dut (
        ._gen_x(reverse17_in), .out(reverse17_out)
    );
    symbolicReverseBlocks #(.W(16), .BLOCKS(2)) reverse_blocks2_dut (
        ._gen_x(reverse_blocks_in), .out(reverse_blocks2_out)
    );

    symbolicReverseBlocks #(.W(16), .BLOCKS(4)) reverse_blocks4_dut (
        ._gen_x(reverse_blocks_in), .out(reverse_blocks4_out)
    );

    symbolicReverseBlocks #(.W(16), .BLOCKS(8)) reverse_blocks8_dut (
        ._gen_x(reverse_blocks_in), .out(reverse_blocks8_out)
    );

    symbolicValueExpression #(.W(3)) value_expr3_dut (
        ._gen_x(value_expr3_in), .out(value_expr3_out)
    );

    symbolicValueExpression #(.W(5)) value_expr5_dut (
        ._gen_x(value_expr5_in), .out(value_expr5_out)
    );

    symbolicBundleAllOutput #(.W(3)) bundle_all3_dut (
        ._gen_x(bundle_all3_in), .out(bundle_all3_out)
    );

    symbolicBundleAllOutput #(.W(5)) bundle_all5_dut (
        ._gen_x(bundle_all5_in), .out(bundle_all5_out)
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
        derived8_in = 3'b101;
        derived12_in = 4'b1101;
        derived16_in = 4'b0110;
        alias8_in = 3'b011;
        alias12_in = 4'b1010;
        alias16_in = 4'b1100;
        loop3_in = 3'b101;
        loop17_in = 17'h1_2345;
        pair3_in = 3'b101;
        pair17_in = 17'h1_2345;
        wide_tuple3_in = 3'b101;
        wide_tuple5_in = 5'b10011;

        depth8_in = 4'd8;
        depth12_in = 4'd12;
        depth16_in = 5'd16;
        mem2_write_addr = 2'd1;
        mem2_write_data = 3'b101;
        mem2_write_enable = 1'b0;
        mem2_read_addr = 2'd0;
        mem4_write_addr = 4'd3;
        mem4_write_data = 17'h1_2345;
        mem4_write_enable = 1'b0;
        mem4_read_addr = 4'd0;
        hier3_lhs = 3'b101;
        hier3_rhs = 3'b011;
        hier17_lhs = 17'h1_5a3c;
        hier17_rhs = 17'h0_0ff0;
        hier65_lhs = {1'b1, 64'h0123_4567_89ab_cdef};
        hier65_rhs = {1'b0, 64'hffff_0000_ffff_0000};
        letmask3_in = 3'b101;
        letmask17_in = 17'h1_2345;
        value_expr3_in = 3'd0;
        value_expr5_in = 5'd0;
        bundle_all3_in = 3'b101;
        bundle_all5_in = 5'b10011;
        generate3_in = 3'b101;
        generate17_in = 17'h1_2468;
        generate65_in = {1'b1, 64'h0123_4567_89ab_cdef};
        popcount3_in = 3'b101;
        popcount17_in = 17'h1ffff;
        uge3_lhs = 3'd5;
        uge3_rhs = 3'd5;
        uge17_lhs = 17'h0_00ff;
        uge17_rhs = 17'h1_0000;
        reverse3_in = 3'b110;
        reverse17_in = 17'h1_0002;
        reverse_blocks_in = 16'h1234;
        cast_extend_in = 65'h1_0000_0000_0000_0001;
        cast_trunc_in = 66'h2_0000_0000_0000_0001;
        modulo_lhs = 17'd12345;
        modulo_rhs = 17'd97;
        repeat_in = 3'b101;
        repeat_alt_in = 5'b10011;
        map_chunks_in = 12'b001_010_100_110;
        map_chunks_indexed_in = 12'b001_010_100_110;
        map_chunks_narrow_in = 12'b1101_0010_1011;
        map_chunks_narrow_alt_in = 20'b0110_1111_0101_1000_0011;
        #1;

        if (concat_out !== {concat_hi, concat_lo})
            $fatal(1, "symbolic concat failed: got %b", concat_out);
        if (slice_out !== slice_in[16:0])
            $fatal(1, "symbolic slice failed: got %b", slice_out);
        if (zext_out !== {1'b0, zext_in})
            $fatal(1, "symbolic zero extension failed: got %b", zext_out);
        if (cast_extend_out !== {1'b0, cast_extend_in})
            $fatal(1, "symbolic cast extension failed: got %b", cast_extend_out);
        if (cast_trunc_out !== cast_trunc_in[64:0])
            $fatal(1, "symbolic cast truncation failed: got %b", cast_trunc_out);
        if (modulo_out !== 17'd26)
            $fatal(1, "symbolic modulo failed: got %0d", modulo_out);
        if (repeat_out !== 12'b101_101_101_101)
            $fatal(1, "symbolic repeat-vector failed: got %b", repeat_out);
        if (repeat_alt_out !== {7{5'b10011}})
            $fatal(1, "symbolic repeat-vector alternate parameters failed: got %b", repeat_alt_out);
        if (iota_out !== 12'b0011_0010_0001)
            $fatal(1, "symbolic iota-vector failed: got %b", iota_out);
        if (iota_alt_out !== 15'b101_100_011_010_001)
            $fatal(1, "symbolic iota-vector alternate parameters failed: got %b", iota_alt_out);
        if (map_chunks_out !== 12'b100_010_001_011)
            $fatal(1, "symbolic map-chunks failed: got %b", map_chunks_out);
        if (map_chunks_indexed_out !== 12'b010_000_101_110)
            $fatal(1, "symbolic indexed map-chunks failed: got %b", map_chunks_indexed_out);
        if (map_chunks_narrow_out !== 6'b01_10_11)
            $fatal(1, "symbolic map-chunks narrow failed: got %b", map_chunks_narrow_out);
        if (map_chunks_narrow_alt_out !== 10'b10_11_01_00_11)
            $fatal(1, "symbolic map-chunks narrow alternate N failed: got %b", map_chunks_narrow_alt_out);
        if (hier3_out !== (hier3_lhs ^ hier3_rhs) ||
            hier17_out !== (hier17_lhs ^ hier17_rhs) ||
            hier65_out !== (hier65_lhs ^ hier65_rhs))
            $fatal(1, "symbolic hierarchy parameter forwarding failed");
        if (letmask3_out !== (letmask3_in ^ 3'd1) ||
            letmask17_out !== (letmask17_in ^ 17'd1))
            $fatal(1, "symbolic let-bound BitVec constant lowering failed");
        if (value_expr3_out !== 3'd5 || value_expr5_out !== 5'd27)
            $fatal(1, "symbolic BitVec value-expression lowering failed");
        if (bundle_all3_out !== {3'b101, 3'b100, 1'b1} ||
            bundle_all5_out !== {5'b10011, 5'b10010, 1'b1})
            $fatal(1, "symbolic bundleAll top-level packing failed");
        if (generate3_out !== ~generate3_in ||
            generate17_out !== ~generate17_in ||
            generate65_out !== ~generate65_in)
            $fatal(1, "symbolic generate behavior failed");
        if (popcount3_out !== 2'd2 || popcount17_out !== 5'd17)
            $fatal(1, "symbolic popcount behavior failed");
        if (uge3_out !== 1'b1 || uge17_out !== 1'b0)
            $fatal(1, "symbolic unsigned greater-or-equal behavior failed");
        if (reverse3_out !== 3'b011 || reverse17_out !== 17'h0_8001)
            $fatal(1, "symbolic reverse-bits behavior failed");
        if (reverse_blocks2_out !== 16'h482c ||
            reverse_blocks4_out !== 16'h84c2 ||
            reverse_blocks8_out !== 16'h2138)
            $fatal(1, "symbolic reverse-blocks behavior failed");

        rst = 1'b1;
        #1;
        if (reg3_out !== 3'd1 || reg17_out !== 17'd1 || reg65_out !== 65'd1)
            $fatal(1, "symbolic register reset sizing failed");
        if (derived8_out !== 3'd0 || derived12_out !== 4'd0 || derived16_out !== 4'd0)
            $fatal(1, "symbolic clog2 loop reset sizing failed");
        if (alias8_out !== 3'd0 || alias12_out !== 4'd0 || alias16_out !== 4'd0)
            $fatal(1, "symbolic clog2 alias reset sizing failed");
        if (depth8_out !== 1'b0 || depth12_out !== 1'b0 || depth16_out !== 1'b0)
            $fatal(1, "parameter-valued symbolic comparison reset failed");
        if (loop3_out !== {3'd0, 1'b1, 1'b0} || loop17_out !== {17'd0, 1'b1, 1'b0})
            $fatal(1, "symbolic-width comparison reset flags failed");
        if (pair3_out !== {3'd0, 3'd0} || pair17_out !== {17'd0, 17'd0})
            $fatal(1, "symbolic packed-pair reset failed");
        if (wide_tuple3_out !== 19'd0 || wide_tuple5_out !== 29'd0)
            $fatal(1, "symbolic wide-tuple reset failed");



        rst = 1'b0;
        #1;
        clk = 1'b1;
        #1;
        if (reg3_out !== reg3_in || reg17_out !== reg17_in || reg65_out !== reg65_in)
            $fatal(1, "symbolic register update failed");
        if (derived8_out !== derived8_in || derived12_out !== derived12_in || derived16_out !== derived16_in)
            $fatal(1, "symbolic clog2 loop update failed");
        if (alias8_out !== alias8_in || alias12_out !== alias12_in || alias16_out !== alias16_in)
            $fatal(1, "symbolic clog2 alias update failed");
        if (loop3_out !== {loop3_in, 1'b0, 1'b0} || loop17_out !== {loop17_in, 1'b0, 1'b0})
            $fatal(1, "symbolic-width comparison update flags failed");
        if (pair3_out !== {pair3_in, 3'd0} || pair17_out !== {pair17_in, 17'd0})
            $fatal(1, "symbolic packed-pair update failed");
        if (wide_tuple3_out !== {wide_tuple3_in, 16'd0} ||
            wide_tuple5_out !== {wide_tuple5_in, 24'd0})
            $fatal(1, "symbolic wide-tuple update failed");

        if (depth8_out !== 1'b1 || depth12_out !== 1'b1 || depth16_out !== 1'b1)
            $fatal(1, "parameter-valued symbolic comparison failed");

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
        if (mem2_read_data !== 3'b101 || mem4_read_data !== 17'h1_2345 ||
            mem2_combo_read_data !== 3'b101 || mem4_combo_read_data !== 17'h1_2345)
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
        if (mem2_read_data !== 3'b011 || mem4_read_data !== 17'h0_abcd ||
            mem2_combo_read_data !== 3'b011 || mem4_combo_read_data !== 17'h0_abcd)
            $fatal(1, "symbolic memory second-address read failed");

        clk = 1'b0;
        rst = 1'b1;
        #1;
        if (mem2_read_data !== 3'b000 || mem4_read_data !== 17'd0 ||
            mem2_combo_read_data !== 3'b000 || mem4_combo_read_data !== 17'd0)
            $fatal(1, "symbolic memory reset did not clear current addresses");

        rst = 1'b0;
        mem2_read_addr = 2'd1;
        mem4_read_addr = 4'd3;
        #1;
        clk = 1'b1;
        #1;
        if (mem2_read_data !== 3'b000 || mem4_read_data !== 17'd0 ||
            mem2_combo_read_data !== 3'b000 || mem4_combo_read_data !== 17'd0)
            $fatal(1, "symbolic memory reset did not clear prior addresses");

        $display("P3_SYMBOLIC_PARAMETER_BEHAVIOR_PASS");
        $finish;
    end
endmodule
