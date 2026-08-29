import Tests.SymbolicParameterCircuits

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private def check (label : String) (ok : Bool) : IO Unit :=
  if ok then pure ()
  else throw <| IO.userError s!"symbolic parameter Lean simulation failed: {label}"

def runSymbolicParameterLeanSimulation : IO Unit := do
  let writeAddr2 : Signal defaultDomain (BitVec 2) :=
    ⟨fun t => BitVec.ofNat 2 (if t == 0 then 1 else 2)⟩
  let writeData8 : Signal defaultDomain (BitVec 8) :=
    ⟨fun t => BitVec.ofNat 8 (if t == 0 then 0x5A else 0xA5)⟩
  let writeEnable2 : Signal defaultDomain Bool := ⟨fun t => t < 2⟩
  let readAddr2 : Signal defaultDomain (BitVec 2) :=
    ⟨fun t => BitVec.ofNat 2 (if t < 2 then 1 else 2)⟩
  let memory2 := symbolicMemory writeAddr2 writeData8 writeEnable2 readAddr2

  check "memory a2/d8 reset" (memory2.atTime 0 == BitVec.ofNat 8 0)
  check "memory a2/d8 registered read" (memory2.atTime 1 == BitVec.ofNat 8 0)
  check "memory a2/d8 first write" (memory2.atTime 2 == BitVec.ofNat 8 0x5A)
  check "memory a2/d8 second write" (memory2.atTime 3 == BitVec.ofNat 8 0xA5)

  let writeAddr4 : Signal defaultDomain (BitVec 4) :=
    ⟨fun t => BitVec.ofNat 4 (if t == 0 then 9 else 10)⟩
  let writeData17 : Signal defaultDomain (BitVec 17) :=
    ⟨fun t => BitVec.ofNat 17 (if t == 0 then 0x12345 else 0x1AAAA)⟩
  let writeEnable4 : Signal defaultDomain Bool := ⟨fun t => t < 2⟩
  let readAddr4 : Signal defaultDomain (BitVec 4) :=
    ⟨fun t => BitVec.ofNat 4 (if t < 2 then 9 else 10)⟩
  let memory4 := symbolicMemory writeAddr4 writeData17 writeEnable4 readAddr4

  check "memory a4/d17 reset" (memory4.atTime 0 == BitVec.ofNat 17 0)
  check "memory a4/d17 registered read" (memory4.atTime 1 == BitVec.ofNat 17 0)
  check "memory a4/d17 first write" (memory4.atTime 2 == BitVec.ofNat 17 0x12345)
  check "memory a4/d17 second write" (memory4.atTime 3 == BitVec.ofNat 17 0x1AAAA)
  IO.println "SYMBOLIC_PARAMETER_LEAN_SIM_PASS"

#eval! runSymbolicParameterLeanSimulation
