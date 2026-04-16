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
private abbrev stWL : BitVec 1 := 0#1  -- Walking Left
private abbrev stWR : BitVec 1 := 1#1  -- Walking Right

/-- Lemmings FSM: 2 states (WalkLeft=0, WalkRight=1).
    Switches direction on bump. Async reset to WalkLeft.
    Returns (walk_left, walk_right) as bundled BitVec 1 signals. -/
def prob127_lemmings1 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 1) :=
    Signal.loop fun (state : Signal dom (BitVec 1)) =>
      let isWL := state === (Signal.pure stWL)
      -- Next state logic:
      --   WL + bump_left  → WR,  WL + no bump → WL
      --   WR + bump_right → WL,  WR + no bump → WR
      -- If bumped on both sides, still switch.
      let nextFromWL := Signal.mux bump_left  (Signal.pure stWR) (Signal.pure stWL)
      let nextFromWR := Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)
      let nextState := Signal.mux isWL nextFromWL nextFromWR
      -- Apply async reset (modeled as sync): areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState
      -- Register with initial value WL
      Signal.register stWL nextWithReset
  -- Derive outputs from state
  -- walk_left = (state == WL), i.e., ~state since WL=0
  -- walk_right = state since WR=1
  let walk_left  := ~~~state
  let walk_right := state
  bundle2 walk_left walk_right

#synthesizeVerilog prob127_lemmings1
