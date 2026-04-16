import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse the bit ordering of an 8-bit input signal. -/
def prob006_vectorr {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun v =>
    -- Reverse bits: out[7]=in[0], out[6]=in[1], ..., out[0]=in[7]
    -- Using BitVec.extractLsb' to get each bit and concatenate in reverse order
    -- Result: {in[0], in[1], in[2], in[3], in[4], in[5], in[6], in[7]}
    BitVec.extractLsb' 0 1 v ++   -- bit 0 → MSB of output
    BitVec.extractLsb' 1 1 v ++
    BitVec.extractLsb' 2 1 v ++
    BitVec.extractLsb' 3 1 v ++
    BitVec.extractLsb' 4 1 v ++
    BitVec.extractLsb' 5 1 v ++
    BitVec.extractLsb' 6 1 v ++
    BitVec.extractLsb' 7 1 v      -- bit 7 → LSB of output
  ) inp

#synthesizeVerilog prob006_vectorr
