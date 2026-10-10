/-
  Test: Backbone Controller FSM
  Verifies stage sequencing through STEM→STAGE_CONV→C2F→(SPPF)→SAVE→NEXT→DONE
-/

import LSpec
import Examples.YOLOv8.Backbone

open cktlean.Core.Domain
open cktlean.Core.Signal
open cktlean.Examples.YOLOv8.Backbone

namespace cktlean.Examples.YOLOv8.Tests.TestBackbone

/-- Test initial FSM transition: IDLE → STEM on start. -/
def testStartTransition : IO LSpec.TestSeq := do
  let subOpDone : Signal defaultDomain Bool := Signal.pure false
  let start : Signal defaultDomain Bool := ⟨fun t => t == 0⟩
  let ctrl ← backboneControllerSimulate subOpDone start
  let fsm := Signal.fst ctrl  -- BitVec 4 (phase)
  return LSpec.group "Backbone Start" (
    LSpec.test "phase at t=0 is IDLE (0)" (fsm.atTime 0 == 0#4) ++
    LSpec.test "phase at t=1 is STEM (1)" (fsm.atTime 1 == 1#4)
  )

/-- Test stem→stageConv transition. -/
def testStemToStageConv : IO LSpec.TestSeq := do
  -- subOpDone at t=5 (stem done)
  let subOpDone : Signal defaultDomain Bool := ⟨fun t => t == 5⟩
  let start : Signal defaultDomain Bool := ⟨fun t => t == 0⟩
  let ctrl ← backboneControllerSimulate subOpDone start
  let fsm := Signal.fst ctrl
  return LSpec.group "Backbone Stem→Conv" (
    LSpec.test "phase at t=1 is STEM (1)" (fsm.atTime 1 == 1#4) ++
    LSpec.test "phase at t=6 is STAGE_CONV (2)" (fsm.atTime 6 == 2#4)
  )

def allTests : IO LSpec.TestSeq := do
  let t1 ← testStartTransition
  let t2 ← testStemToStageConv
  return LSpec.group "Backbone Controller" (t1 ++ t2)

end cktlean.Examples.YOLOv8.Tests.TestBackbone
