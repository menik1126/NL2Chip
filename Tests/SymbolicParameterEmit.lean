import Sparkle.Compiler.Elab
import Tests.SymbolicParameterCircuits

#synthesizeParameterizedVerilog symbolicIdentity [W := 8]
#synthesizeParameterizedVerilog symbolicXor [W := 8]
#synthesizeParameterizedVerilog symbolicConcat [HI := 5, LO := 3]
#synthesizeParameterizedVerilog symbolicSliceLow [W := 8]
#synthesizeParameterizedVerilog symbolicZeroExtend [W := 8]
#synthesizeParameterizedVerilog symbolicRegister [W := 8]
#synthesizeParameterizedVerilog symbolicMemory [ADDR_W := 3, DATA_W := 8]
#synthesizeParameterizedVerilogDesign symbolicXorHierarchy [W := 8]
#synthesizeParameterizedVerilog symbolicLoopXor [W := 8]
#synthesizeParameterizedVerilog symbolicDerivedLoop [DEPTH := 8]
#synthesizeParameterizedVerilog symbolicDerivedAlias [DEPTH := 8]
#synthesizeParameterizedVerilog symbolicLoopBundle [W := 8]
#synthesizeParameterizedVerilog symbolicDepthCompare [DEPTH := 8]
#synthesizeParameterizedVerilog symbolicGenerateNot [W := 8]
