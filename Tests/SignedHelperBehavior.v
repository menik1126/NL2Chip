module signed_helper_behavior_tb;
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

    initial begin
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

        sext_in = 4'h3;
        asr_in = 4'h8;
        asr_amount = 4'd3;
        lt_lhs = 4'h2;
        lt_rhs = 4'hd;
        mul_lhs = 4'hd;
        mul_rhs = 4'he;
        mul_shift_amount = 8'd2;
        sat_value = 8'h9c;
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

        sat_value = 8'h05;
        #1;
        if (sat_out !== 8'h05)
            $fatal(1, "in-range signed saturation failed");

        $display("SIGNED_HELPER_SV_BEHAVIOR_PASS");
        $finish;
    end
endmodule
