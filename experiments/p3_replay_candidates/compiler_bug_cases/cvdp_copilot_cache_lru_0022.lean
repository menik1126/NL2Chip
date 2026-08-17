import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

open Sparkle.Library.RTL

/-- FIFO-based cache replacement policy. Updates way_replace only on access misses. -/
def fifo_policy {dom : DomainConfig}
    (index : Signal dom (BitVec 5))
    (way_select : Signal dom (BitVec 2))
    (access : Signal dom Bool)
    (hit : Signal dom Bool)
    (reset : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  -- Read current fifo_array value for this index
  let current := regFile1R1W index (Signal.pure 0#2) (Signal.pure false) index

  -- Update condition: access AND NOT hit (i.e., a miss)
  let miss := Signal.mux hit (Signal.pure false) (Signal.pure true)
  let should_update := Signal.mux access miss (Signal.pure false)

  -- Next value: increment on miss
  let next_val := current + 1#2

  -- Choose whether to update or keep current
  let updated := Signal.mux should_update next_val current

  -- Apply reset: when reset is high, write 0 to the array
  let write_enable := reset ||| should_update
  let write_data := Signal.mux reset (Signal.pure 0#2) next_val

  -- Register file with write logic
  regFile1R1W index write_data write_enable index

#synthesizeVerilog fifo_policy
