import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Generic combinational extended Hamming-code transmitter. -/
def hamming_tx {dom : DomainConfig} {DATA_WIDTH PARITY_BIT : Nat}
    (data_in : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec (DATA_WIDTH + PARITY_BIT + 1)) :=
  let E := DATA_WIDTH + PARITY_BIT + 1
  Signal.map (fun din =>
    let positions := List.range E
    let placed := positions.foldl (fun acc pos =>
      if pos == 0 || (pos &&& (pos - 1)) == 0 then acc
      else
        let di := (List.range pos).foldl (fun n p =>
          if p != 0 && (p &&& (p - 1)) != 0 then n + 1 else n) 0
        if din.getLsbD di then acc + 2 ^ pos else acc) 0
    let encoded := (List.range PARITY_BIT).foldl (fun acc pn =>
      let pp := 2 ^ pn
      let parity := positions.foldl (fun v pos =>
        if (pos &&& pp) != 0 && ((placed / (2 ^ pos)) % 2 == 1)
        then !v else v) false
      if parity then acc + 2 ^ pp else acc) placed
    BitVec.ofNat E encoded) data_in

#synthesizeParameterizedVerilog hamming_tx [DATA_WIDTH := 4, PARITY_BIT := 3]
