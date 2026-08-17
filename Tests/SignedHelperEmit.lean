import Sparkle.Compiler.Elab
import Tests.SignedHelperCircuits

#synthesizeParameterizedVerilog signedHelperSignExtend [W := 4]
#synthesizeParameterizedVerilog signedHelperArithmeticShiftRight [W := 4]
#synthesizeParameterizedVerilog signedHelperLT [W := 4]
#synthesizeParameterizedVerilog signedHelperMulWide [W := 4]
#synthesizeParameterizedVerilog signedHelperMulTrunc [W := 4]
#synthesizeParameterizedVerilog signedHelperMulShiftTrunc [W := 4]
#synthesizeParameterizedVerilog signedHelperSaturate [W := 8]
