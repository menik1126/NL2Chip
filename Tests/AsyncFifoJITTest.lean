import Sparkle.Core.JIT
import Tests.AsyncFifoElab

open Sparkle.Core.JIT

private def require (condition : Bool) (message : String) : IO Unit :=
  unless condition do throw (IO.userError message)

private def domainIndex (domains : Array DomainInfo) (name : String) : IO UInt32 := do
  match domains.find? (fun domain => domain.name == name) with
  | some domain => return domain.index
  | none => throw (IO.userError s!"missing JIT domain '{name}': {reprStr domains}")

private def testFifo (jitPath : String) (depth : Nat) (firstValue : UInt64) : IO Unit := do
  let handle ← JIT.compileAndLoad jitPath
  try
    let domains ← JIT.domains handle
    require (domains.size == 2) s!"expected two FIFO domains, got {domains.size}"
    let writeDomain ← domainIndex domains "write"
    let readDomain ← domainIndex domains "read"

    JIT.reset handle
    JIT.setInput handle 0 0
    JIT.setInput handle 1 0
    JIT.setInput handle 2 0
    JIT.eval handle
    require ((← JIT.getOutput handle 0) == 0) "FIFO unexpectedly full after reset"
    require ((← JIT.getOutput handle 2) == 1) "FIFO must be empty after reset"

    JIT.setInput handle 0 1
    JIT.setInput handle 1 firstValue
    JIT.evalTickDomain handle writeDomain
    JIT.setInput handle 0 0
    JIT.eval handle
    require ((← JIT.getOutput handle 1) == firstValue)
      "FWFT read data did not expose the first written word"

    for _ in [:3] do
      JIT.evalTickDomain handle readDomain
    JIT.eval handle
    require ((← JIT.getOutput handle 2) == 0)
      "read domain did not observe the synchronized write pointer"

    JIT.setInput handle 2 1
    JIT.evalTickDomain handle readDomain
    JIT.setInput handle 2 0
    JIT.eval handle
    require ((← JIT.getOutput handle 2) == 1)
      "FIFO did not become empty after consuming its only word"

    JIT.reset handle
    for value in [:depth] do
      JIT.setInput handle 0 1
      JIT.setInput handle 1 (UInt64.ofNat (value + 1))
      JIT.evalTickDomain handle writeDomain
    JIT.setInput handle 0 0
    JIT.eval handle
    require ((← JIT.getOutput handle 0) == 1)
      "FIFO full flag did not assert at its configured depth"

  finally
    JIT.destroy handle

def main : IO UInt32 := do
  testFifo ".lake/build/gen/tests/async_fifo_jit.cpp" 8 0xA5
  testFifo ".lake/build/gen/tests/async_fifo_4x17_jit.cpp" 4 0x1A5A5
  IO.println "async FIFO 8x8 and 4x17 multi-domain/FWFT JIT: PASS"
  return 0
