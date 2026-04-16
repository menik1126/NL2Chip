/-
  VerilogEval Prob127: Lemmings FSM (Walk Left / Walk Right)

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  clk
   - input  areset
   - input  bump_left
   - input  bump_right
   - output walk_left
   - output walk_right
  The module should implement a simple game called Lemmings which involves
  critters with fairly simple brains. In the Lemmings' 2D world, Lemmings
  can be in one of two states: walking left (walk_left is 1) or walking
  right (walk_right is 1). It will switch directions if it hits an
  obstacle. In particular, if a Lemming is bumped on the left (bump_left),
  it will walk right. If bumped on the right (bump_right), it will walk
  left. If bumped on both sides at the same time, it will still switch
  directions.

  The module should implement a Moore state machine with two states.
  areset is positive edge triggered asynchronous, resetting the Lemming
  to walk left. Assume all sequential logic is triggered on the positive
  edge of the clock.

  Reference Verilog:
  module RefModule (
    input clk, input areset, input bump_left, input bump_right,
    output walk_left, output walk_right);
    parameter WL=0, WR=1;
    reg state, next;
    always_comb begin
      case (state)
        WL: next = bump_left  ? WR : WL;
        WR: next = bump_right ? WL : WR;
      endcase
    end
    always @(posedge clk, posedge areset) begin
      if (areset) state <= WL;
      else        state <= next;
    end
    assign walk_left  = (state==WL);
    assign walk_right = (state==WR);
  endmodule

  Note: Uses Signal.loop for state feedback. State encoded as BitVec 1:
  WL=0, WR=1. Outputs walk_left and walk_right are derived from state.
  areset modeled as synchronous reset via mux.
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stBYTE1 : BitVec 2 := 0#2
private abbrev stBYTE2 : BitVec 2 := 1#2
private abbrev stBYTE3 : BitVec 2 := 2#2
private abbrev stDONE  : BitVec 2 := 3#2

/-- PS/2 mouse protocol FSM -/
def prob128_fsm_ps2 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- Extract in[3] - shift right by 3 and check if bit 0 is set
  let shifted : Signal dom (BitVec 8) := inp >>> 3#8
  let masked : Signal dom (BitVec 8) := shifted &&& 1#8
  let in3 : Signal dom Bool := masked === 1#8
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isBYTE1 := state === (Signal.pure stBYTE1)
      let isBYTE2 := state === (Signal.pure stBYTE2)
      let isBYTE3 := state === (Signal.pure stBYTE3)
      -- Next state logic:
      --   BYTE1: if in3 then BYTE2 else BYTE1
      --   BYTE2: always go to BYTE3
      --   BYTE3: always go to DONE
      --   DONE: if in3 then BYTE2 else BYTE1
      let nextFromBYTE1 := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      let nextFromBYTE2 := Signal.pure stBYTE3
      let nextFromBYTE3 := Signal.pure stDONE
      let nextFromDONE := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      let nextState := Signal.mux isBYTE1 nextFromBYTE1
        (Signal.mux isBYTE2 nextFromBYTE2
          (Signal.mux isBYTE3 nextFromBYTE3 nextFromDONE))
      -- Apply reset
      let nextWithReset := Signal.mux reset (Signal.pure stBYTE1) nextState
      -- Register with initial value BYTE1
      Signal.register stBYTE1 nextWithReset
  -- Derive output from state
  -- done = (state == DONE)
  let isDONE := state === (Signal.pure stDONE)
  Signal.mux isDONE (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob128_fsm_ps2
