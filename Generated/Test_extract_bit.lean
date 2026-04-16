import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_extract_bit {dom : DomainConfig}
    (a : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  (a >>> 7#8) &&& 1#8

#synthesizeVerilog test_extract_bit
