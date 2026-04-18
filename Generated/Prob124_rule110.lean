import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Helper function to compute Rule 110 next state
def rule110NextState (q : BitVec 512) : BitVec 512 :=
  let left := BitVec.ushiftRight q 1
  let center := q
  let right := BitVec.shiftLeft q 1
  
  -- Rule 110 logic: next = ~((L&C&R) | (~L&~C&~R) | (L&~C&~R))
  let lcr := left &&& center &&& right
  let notLnotCnotR := (~~~left) &&& (~~~center) &&& (~~~right)
  let lNotCnotR := left &&& (~~~center) &&& (~~~right)
  let combined := lcr ||| notLnotCnotR ||| lNotCnotR
  ~~~combined

/-- Rule 110 cellular automaton with 512 cells -/
def prob124_rule110 {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 512)) : Signal dom (BitVec 512) :=
  Signal.loop fun (q : Signal dom (BitVec 512)) =>
    let nextState := Signal.map rule110NextState q
    let nextVal := Signal.mux load data nextState
    Signal.register (BitVec.zero 512) nextVal

#synthesizeVerilog prob124_rule110
