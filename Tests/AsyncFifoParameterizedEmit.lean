import Sparkle.Compiler.Elab
import Tests.AsyncFifoElab

open Tests.AsyncFifoElab

#synthesizeParameterizedVerilog Tests.AsyncFifoElab.parameterizedFifoTop
  [DEPTH := 8, DATA_WIDTH := 17]

#writeParameterizedVerilogDesign Tests.AsyncFifoElab.parameterizedFifoTop
  [DEPTH := 8, DATA_WIDTH := 17]
  ".lake/build/gen/tests/async_fifo_parameterized.sv"

#writeParameterizedCppSimDesign Tests.AsyncFifoElab.parameterizedFifoTop
  [DEPTH := 4, DATA_WIDTH := 17]
  ".lake/build/gen/tests/async_fifo_parameterized_4x17_cppsim.h"
