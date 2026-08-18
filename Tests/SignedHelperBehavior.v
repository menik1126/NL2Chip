module signed_helper_behavior_tb;
    logic clk;
    logic rst;
    logic [3:0] sext_in;
    wire [7:0] sext_out;
    logic [3:0] asr_in;
    logic [3:0] asr_amount;
    wire [3:0] asr_out;
    logic [3:0] lt_lhs;
    logic [3:0] lt_rhs;
    wire lt_out;
    logic [3:0] mul_lhs;
    logic [3:0] mul_rhs;
    wire [7:0] mul_wide_out;
    wire [3:0] mul_trunc_out;
    logic [7:0] mul_shift_amount;
    wire [3:0] mul_shift_out;
    logic [7:0] sat_value;
    logic [7:0] sat_lower;
    logic [7:0] sat_upper;
    wire [7:0] sat_out;
    logic [3:0] arithmetic_lhs;
    logic [3:0] arithmetic_rhs;
    wire [7:0] add_to_out;
    wire [7:0] sub_to_out;
    wire [7:0] mul_to_out;
    logic [3:0] div_numerator;
    logic [3:0] div_denominator;
    logic [3:0] div_fallback;
    wire [3:0] unsigned_div_out;
    wire [3:0] signed_div_out;
    logic [3:0] abs_in;
    wire [4:0] abs_out;
    logic [3:0] mean_lhs;
    logic [3:0] mean_rhs;
    wire [3:0] mean_tz_out;
    wire [3:0] mean_floor_out;
    logic [3:0] div_pow2_value;
    logic [4:0] div_pow2_amount;
    wire [3:0] div_pow2_out;
    logic [11:0] dot_lhs;
    logic [8:0] dot_rhs;
    wire [9:0] dot_out;
    wire [9:0] sum_out;
    wire [11:0] reverse_lanes_out;
    wire [9:0] reversed_dot_out;
    logic stage_reset;
    logic stage_enable;
    logic [3:0] stage_lhs;
    logic [3:0] stage_rhs;
    logic [3:0] stage_bias;
    wire [13:0] stage_out;

    signedHelperSignExtend #(.W(4)) sext_dut (
        ._gen_x(sext_in), .out(sext_out)
    );
    signedHelperArithmeticShiftRight #(.W(4)) asr_dut (
        ._gen_x(asr_in), ._gen_amount(asr_amount), .out(asr_out)
    );
    signedHelperLT #(.W(4)) lt_dut (
        ._gen_lhs(lt_lhs), ._gen_rhs(lt_rhs), .out(lt_out)
    );
    signedHelperMulWide #(.W(4)) mul_wide_dut (
        ._gen_lhs(mul_lhs), ._gen_rhs(mul_rhs), .out(mul_wide_out)
    );
    signedHelperMulTrunc #(.W(4)) mul_trunc_dut (
        ._gen_lhs(mul_lhs), ._gen_rhs(mul_rhs), .out(mul_trunc_out)
    );
    signedHelperMulShiftTrunc #(.W(4)) mul_shift_dut (
        ._gen_lhs(mul_lhs), ._gen_rhs(mul_rhs),
        ._gen_amount(mul_shift_amount), .out(mul_shift_out)
    );
    signedHelperSaturate #(.W(8)) sat_dut (
        ._gen_value(sat_value), ._gen_lower(sat_lower),
        ._gen_upper(sat_upper), .out(sat_out)
    );
    signedHelperAddTo #(.W(4)) add_to_dut (
        ._gen_lhs(arithmetic_lhs), ._gen_rhs(arithmetic_rhs), .out(add_to_out)
    );
    signedHelperSubTo #(.W(4)) sub_to_dut (
        ._gen_lhs(arithmetic_lhs), ._gen_rhs(arithmetic_rhs), .out(sub_to_out)
    );
    signedHelperMulTo #(.W(4)) mul_to_dut (
        ._gen_lhs(arithmetic_lhs), ._gen_rhs(arithmetic_rhs), .out(mul_to_out)
    );
    signedHelperUnsignedDivOr #(.W(4)) unsigned_div_dut (
        ._gen_numerator(div_numerator), ._gen_denominator(div_denominator),
        ._gen_fallback(div_fallback), .out(unsigned_div_out)
    );
    signedHelperSignedDivOr #(.W(4)) signed_div_dut (
        ._gen_numerator(div_numerator), ._gen_denominator(div_denominator),
        ._gen_fallback(div_fallback), .out(signed_div_out)
    );
    signedHelperAbsTo #(.W(4)) abs_dut (
        ._gen_value(abs_in), .out(abs_out)
    );
    signedHelperMeanTowardZero #(.W(4)) mean_tz_dut (
        ._gen_lhs(mean_lhs), ._gen_rhs(mean_rhs), .out(mean_tz_out)
    );
    signedHelperMeanFloor #(.W(4)) mean_floor_dut (
        ._gen_lhs(mean_lhs), ._gen_rhs(mean_rhs), .out(mean_floor_out)
    );
    signedHelperDivPow2TowardZero #(.W(4)) div_pow2_dut (
        ._gen_value(div_pow2_value), ._gen_amount(div_pow2_amount),
        .out(div_pow2_out)
    );
    signedHelperDotPacked #(
        .LANES(3), .LHSW(4), .RHSW(3), .ACCW(10)
    ) dot_dut (
        ._gen_lhs(dot_lhs), ._gen_rhs(dot_rhs), .out(dot_out)
    );
    signedHelperSumPacked #(
        .LANES(3), .W(4), .ACCW(10)
    ) sum_dut (
        ._gen_values(dot_lhs), .out(sum_out)
    );
    signedHelperReversePackedLanes #(
        .LANES(3), .W(4)
    ) reverse_lanes_dut (
        ._gen_values(dot_lhs), .out(reverse_lanes_out)
    );
    signedHelperReversedDotPacked #(
        .LANES(3), .LHSW(4), .RHSW(3), .ACCW(10)
    ) reversed_dot_dut (
        ._gen_lhs(dot_lhs), ._gen_rhs(dot_rhs), .out(reversed_dot_out)
    );
    signedHelperRegisteredStage #(.W(4)) registered_stage_dut (
        ._gen_reset(stage_reset), ._gen_enable(stage_enable),
        ._gen_lhs(stage_lhs), ._gen_rhs(stage_rhs), ._gen_bias(stage_bias),
        .clk(clk), .rst(rst), .out(stage_out)
    );

    always #1 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst = 1'b1;
        stage_reset = 1'b0;
        stage_enable = 1'b0;
        stage_lhs = 4'h0;
        stage_rhs = 4'h0;
        stage_bias = 4'h0;
        sext_in = 4'hd;
        asr_in = 4'hd;
        asr_amount = 4'd1;
        lt_lhs = 4'hd;
        lt_rhs = 4'h2;
        mul_lhs = 4'hd;
        mul_rhs = 4'h2;
        mul_shift_amount = 8'd2;
        sat_value = 8'h64;
        sat_lower = 8'hf6;
        sat_upper = 8'h0a;
        arithmetic_lhs = 4'hd;
        arithmetic_rhs = 4'h2;
        div_numerator = 4'hd;
        div_denominator = 4'h2;
        div_fallback = 4'h9;
        abs_in = 4'hd;
        mean_lhs = 4'hd;
        mean_rhs = 4'h0;
        div_pow2_value = 4'hd;
        div_pow2_amount = 5'd1;
        dot_lhs = 12'hf2d;
        dot_rhs = 9'h1f2;
        #1;

        if (sext_out !== 8'hfd)
            $fatal(1, "signed sign extension failed: got %h", sext_out);
        if (asr_out !== 4'he)
            $fatal(1, "signed arithmetic right shift failed: got %h", asr_out);
        if (lt_out !== 1'b1)
            $fatal(1, "signed comparison failed: got %b", lt_out);
        if (mul_wide_out !== 8'hfa)
            $fatal(1, "signed wide multiply failed: got %h", mul_wide_out);
        if (mul_trunc_out !== 4'ha)
            $fatal(1, "signed truncated multiply failed: got %h", mul_trunc_out);
        if (mul_shift_out !== 4'he)
            $fatal(1, "signed fixed-point multiply/shift failed: got %h", mul_shift_out);
        if (sat_out !== 8'h0a)
            $fatal(1, "positive signed saturation failed");
        if (add_to_out !== 8'hff)
            $fatal(1, "caller-width signed addition failed: got %h", add_to_out);
        if (sub_to_out !== 8'hfb)
            $fatal(1, "caller-width signed subtraction failed: got %h", sub_to_out);
        if (mul_to_out !== 8'hfa)
            $fatal(1, "caller-width signed multiplication failed: got %h", mul_to_out);
        if (unsigned_div_out !== 4'h6)
            $fatal(1, "unsigned division failed: got %h", unsigned_div_out);
        if (signed_div_out !== 4'hf)
            $fatal(1, "signed division did not truncate toward zero: got %h", signed_div_out);
        if (abs_out !== 5'h03)
            $fatal(1, "signed absolute value failed: got %h", abs_out);
        if (mean_tz_out !== 4'hf)
            $fatal(1, "signed mean toward zero failed: got %h", mean_tz_out);
        if (mean_floor_out !== 4'he)
            $fatal(1, "signed mean floor failed: got %h", mean_floor_out);
        if (div_pow2_out !== 4'hf)
            $fatal(1, "signed power-of-two division failed: got %h", div_pow2_out);
        if (dot_out !== 10'h3f7)
            $fatal(1, "signed packed dot product failed: got %h", dot_out);
        if (sum_out !== 10'h3fe)
            $fatal(1, "signed packed sum failed: got %h", sum_out);
        if (reverse_lanes_out !== 12'hd2f)
            $fatal(1, "packed lane reversal failed: got %h", reverse_lanes_out);
        if (reversed_dot_out !== 10'h3fd)
            $fatal(1, "signed reversed-lane dot product failed: got %h", reversed_dot_out);

        sext_in = 4'h3;
        asr_in = 4'h8;
        asr_amount = 4'd3;
        lt_lhs = 4'h2;
        lt_rhs = 4'hd;
        mul_lhs = 4'hd;
        mul_rhs = 4'he;
        mul_shift_amount = 8'd2;
        sat_value = 8'h9c;
        div_denominator = 4'h0;
        #1;

        if (sext_out !== 8'h03)
            $fatal(1, "positive sign extension failed: got %h", sext_out);
        if (asr_out !== 4'hf)
            $fatal(1, "negative arithmetic shift rounding failed: got %h", asr_out);
        if (lt_out !== 1'b0)
            $fatal(1, "signed comparison reversal failed: got %b", lt_out);
        if (mul_wide_out !== 8'h06)
            $fatal(1, "negative times negative multiply failed: got %h", mul_wide_out);
        if (mul_trunc_out !== 4'h6)
            $fatal(1, "negative times negative truncation failed: got %h", mul_trunc_out);
        if (mul_shift_out !== 4'h1)
            $fatal(1, "negative fixed-point multiply/shift failed: got %h", mul_shift_out);
        if (sat_out !== 8'hf6)
            $fatal(1, "negative signed saturation failed");
        if (unsigned_div_out !== 4'h9 || signed_div_out !== 4'h9)
            $fatal(1, "division fallback failed: unsigned=%h signed=%h",
                unsigned_div_out, signed_div_out);

        sat_value = 8'h05;
        #1;
        if (sat_out !== 8'h05)
            $fatal(1, "in-range signed saturation failed");

        rst = 1'b0;
        stage_enable = 1'b1;
        stage_lhs = 4'hd;
        stage_rhs = 4'h2;
        stage_bias = 4'h2;
        @(posedge clk);
        #1;
        if (stage_out[13:6] !== 8'hfa || stage_out[5:1] !== 5'h01 ||
            stage_out[0] !== 1'b1)
            $fatal(1, "symbolic registered signed stage failed: got %h", stage_out);

        stage_enable = 1'b0;
        stage_lhs = 4'h7;
        stage_rhs = 4'h7;
        stage_bias = 4'h7;
        @(posedge clk);
        #1;
        if (stage_out[13:6] !== 8'hfa || stage_out[5:1] !== 5'h01 ||
            stage_out[0] !== 1'b0)
            $fatal(1, "registered signed stage hold/valid failed: got %h", stage_out);

        $display("SIGNED_HELPER_SV_BEHAVIOR_PASS");
        $finish;
    end
endmodule
