import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with a 4x4 grid (16 cells) to see if the approach works -/
def computeNextGrid4x4 (g : BitVec 16) : BitVec 16 :=
  let r0 := 0#16
  -- Cell 0 (row 0, col 0)
  let a0 := g.getLsb ⟨0, by omega⟩
  let n0 := (if g.getLsb ⟨15, by omega⟩ then 1 else 0) + (if g.getLsb ⟨12, by omega⟩ then 1 else 0) + (if g.getLsb ⟨13, by omega⟩ then 1 else 0) + (if g.getLsb ⟨3, by omega⟩ then 1 else 0) + (if g.getLsb ⟨1, by omega⟩ then 1 else 0) + (if g.getLsb ⟨7, by omega⟩ then 1 else 0) + (if g.getLsb ⟨4, by omega⟩ then 1 else 0) + (if g.getLsb ⟨5, by omega⟩ then 1 else 0)
  let r1 := if (n0 == 2 && a0) || n0 == 3 then r0 ||| (1#16 <<< 0) else r0
  -- Cell 1
  let a1 := g.getLsb ⟨1, by omega⟩
  let n1 := (if g.getLsb ⟨12, by omega⟩ then 1 else 0) + (if g.getLsb ⟨13, by omega⟩ then 1 else 0) + (if g.getLsb ⟨14, by omega⟩ then 1 else 0) + (if g.getLsb ⟨0, by omega⟩ then 1 else 0) + (if g.getLsb ⟨2, by omega⟩ then 1 else 0) + (if g.getLsb ⟨4, by omega⟩ then 1 else 0) + (if g.getLsb ⟨5, by omega⟩ then 1 else 0) + (if g.getLsb ⟨6, by omega⟩ then 1 else 0)
  let r2 := if (n1 == 2 && a1) || n1 == 3 then r1 ||| (1#16 <<< 1) else r1
  r2

/-- Test function -/
def test4x4 {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 16))
    : Signal dom (BitVec 16) :=
  Signal.loop fun (q : Signal dom (BitVec 16)) =>
    let nextState := Signal.map computeNextGrid4x4 q
    let nextQ := Signal.mux load data nextState
    Signal.register 0#16 nextQ

#synthesizeVerilog test4x4
