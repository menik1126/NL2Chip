import Sparkle.Compiler.Elab
import Tests.SymbolicParameterCircuits

#synthesizeParameterizedVerilog symbolicIdentity [W := 8]
#synthesizeParameterizedVerilog symbolicXor [W := 8]
