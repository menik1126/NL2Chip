module prob004_vector2 (_gen_input,
    out);
 input [31:0] _gen_input;
 output [31:0] out;


 assign out[0] = _gen_input[24];
 assign out[10] = _gen_input[18];
 assign out[11] = _gen_input[19];
 assign out[12] = _gen_input[20];
 assign out[13] = _gen_input[21];
 assign out[14] = _gen_input[22];
 assign out[15] = _gen_input[23];
 assign out[16] = _gen_input[8];
 assign out[17] = _gen_input[9];
 assign out[18] = _gen_input[10];
 assign out[19] = _gen_input[11];
 assign out[1] = _gen_input[25];
 assign out[20] = _gen_input[12];
 assign out[21] = _gen_input[13];
 assign out[22] = _gen_input[14];
 assign out[23] = _gen_input[15];
 assign out[24] = _gen_input[0];
 assign out[25] = _gen_input[1];
 assign out[26] = _gen_input[2];
 assign out[27] = _gen_input[3];
 assign out[28] = _gen_input[4];
 assign out[29] = _gen_input[5];
 assign out[2] = _gen_input[26];
 assign out[30] = _gen_input[6];
 assign out[31] = _gen_input[7];
 assign out[3] = _gen_input[27];
 assign out[4] = _gen_input[28];
 assign out[5] = _gen_input[29];
 assign out[6] = _gen_input[30];
 assign out[7] = _gen_input[31];
 assign out[8] = _gen_input[16];
 assign out[9] = _gen_input[17];
endmodule
