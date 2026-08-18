import Sparkle.Compiler.Elab
import Tests.IndexedParameterCircuits

#synthesizeParameterizedVerilogDesign indexedGenerateIdentity [W := 8]
#synthesizeParameterizedVerilogDesign indexedGenerateChunks [N := 3]
#synthesizeParameterizedVerilog indexedScatter [DATAW := 4, PARITYW := 3]
#synthesizeParameterizedVerilog indexedParity [W := 8, PARITYW := 3]
#synthesizeParameterizedVerilog indexedPlaceParity [DATAW := 4, PARITYW := 3]
#synthesizeParameterizedVerilog indexedGather [DATAW := 4, PARITYW := 3]
#synthesizeParameterizedVerilog indexedRoundTrip [DATAW := 4, PARITYW := 3]
#synthesizeParameterizedVerilog indexedHammingEncode [DATAW := 4, PARITYW := 3]
#synthesizeParameterizedVerilog indexedHammingDecode [DATAW := 4, PARITYW := 3]
