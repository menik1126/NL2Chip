import Sparkle.Compiler.Elab
import Tests.SignedHelperCircuits

#synthesizeParameterizedVerilog signedHelperSignExtend [W := 4]
#synthesizeParameterizedVerilog signedHelperArithmeticShiftRight [W := 4]
#synthesizeParameterizedVerilog signedHelperLT [W := 4]
#synthesizeParameterizedVerilog signedHelperMulWide [W := 4]
#synthesizeParameterizedVerilog signedHelperMulTrunc [W := 4]
#synthesizeParameterizedVerilog signedHelperMulShiftTrunc [W := 4]
#synthesizeParameterizedVerilog signedHelperSaturate [W := 8]
#synthesizeParameterizedVerilog signedHelperAddTo [W := 4]
#synthesizeParameterizedVerilog signedHelperSubTo [W := 4]
#synthesizeParameterizedVerilog signedHelperMulTo [W := 4]
#synthesizeParameterizedVerilog signedHelperUnsignedDivOr [W := 4]
#synthesizeParameterizedVerilog signedHelperSignedDivOr [W := 4]
#synthesizeParameterizedVerilog signedHelperAbsTo [W := 4]
#synthesizeParameterizedVerilog signedHelperMeanTowardZero [W := 4]
#synthesizeParameterizedVerilog signedHelperMeanFloor [W := 4]
#synthesizeParameterizedVerilog signedHelperDivPow2TowardZero [W := 4]
#synthesizeParameterizedVerilog signedHelperDotPacked
  [LANES := 3, LHSW := 4, RHSW := 3, ACCW := 10]
#synthesizeParameterizedVerilog signedHelperSumPacked
  [LANES := 3, W := 4, ACCW := 10]
#synthesizeParameterizedVerilog signedHelperReversePackedLanes
  [LANES := 3, W := 4]
#synthesizeParameterizedVerilog signedHelperReversedDotPacked
  [LANES := 3, LHSW := 4, RHSW := 3, ACCW := 10]
#synthesizeParameterizedVerilog signedHelperRegisteredStage [W := 4]
