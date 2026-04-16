/-
  VerilogEval Prob109: Moore FSM (2 states)

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  clk
   - input  areset
   - input  in
   - output out
  The module should implement a Moore machine with the following
  state diagram:
    B (out=1) --0--> A
    B (out=1) --1--> B
    A (out=0) --0--> B
    A (out=0) --1--> A
  It should asynchronously reset into state B if reset is high.

  Reference Verilog:
  module RefModule (input clk, input in, input areset, output out);
    parameter A=0, B=1;
    reg state, next;
    always_comb begin
      case (state)
        A: next = in ? A : B;
        B: next = in ? B : A;
      endcase
    end
    always @(posedge clk, posedge areset) begin
      if (areset) state <= B;
      else        state <= next;
    end
    assign out = (state==B);
  endmodule

  Note: Uses Signal.loop for state feedback. State encoded as BitVec 1:
  A = 0, B = 1. The areset is modeled as a synchronous reset via mux
  (Sparkle's DomainConfig handles async reset at the domain level;
  for explicit areset signals, mux-based modeling is the DSL pattern).
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Moore FSM: 2 states (A=0, B=1), output is 1 when in state B.
    Async reset to state B modeled as synchronous mux. -/
def prob109_fsm1 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Next state logic:
    --   A + in=0 → B,  A + in=1 → A
    --   B + in=0 → A,  B + in=1 → B
    -- Observation: when in=1, state stays; when in=0, state flips.
    -- next = in ? state : ~state
    let stateFlipped := ~~~state
    let nextState := Signal.mux inp state stateFlipped
    -- Apply async reset (modeled as sync): areset → B
    let nextWithReset := Signal.mux areset (Signal.pure stB) nextState
    -- Register with initial value B (matches areset behavior)
    let regState := Signal.register stB nextWithReset
    -- Output: state == B (i.e., state itself since B=1)
    -- But we need to return the registered state for the loop
    regState

#synthesizeVerilog prob109_fsm1
