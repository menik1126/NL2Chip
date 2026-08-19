import Sparkle.Compiler.Elab
import Tests.SignedHelperCircuits

#synthesizeVerilog signedHelperSignExtend4
#synthesizeVerilog signedHelperArithmeticShiftRight parameters [W := 4]
#synthesizeVerilog signedHelperLT parameters [W := 4]
#synthesizeVerilog signedHelperMulWide4
#synthesizeVerilog signedHelperSaturate parameters [W := 8]
#synthesizeVerilog signedHelperUnsignedDivOr parameters [W := 4]
#synthesizeVerilog signedHelperSignedDivOr parameters [W := 4]
#synthesizeVerilog signedHelperDotPacked parameters
  [LANES := 3, LHSW := 4, RHSW := 3, ACCW := 10]
