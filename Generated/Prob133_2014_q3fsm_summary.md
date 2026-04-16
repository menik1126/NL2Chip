# Prob133_2014_q3fsm - FSM for Pattern Detection

## Problem Summary
Implement an FSM that:
1. Starts in state A, waiting for input s=1
2. Once s=1, moves to state B and begins monitoring input w
3. For every 3 consecutive clock cycles, counts how many times w=1
4. Outputs z=1 in the cycle immediately after a 3-cycle window where exactly 2 w values were 1
5. Continues monitoring in 3-cycle windows indefinitely

## Implementation Approach

**State Encoding (8 states, 3 bits):**
- A (0): Initial/waiting state
- B (1): Start of 3-cycle window, no z output
- C (2): Start of 3-cycle window, WITH z=1 output
- S10 (3): After 1 cycle, seen 0 ones
- S11 (4): After 1 cycle, seen 1 one
- S20 (5): After 2 cycles, seen 0 ones
- S21 (6): After 2 cycles, seen 1 one
- S22 (7): After 2 cycles, seen 2 ones

**State Transitions:**
- From S20 (0 ones after 2 cycles): always → B (impossible to get exactly 2)
- From S21 (1 one after 2 cycles): 
  - if w=1 → C (total 2 ones, output z!)
  - if w=0 → B (total 1 one, no output)
- From S22 (2 ones after 2 cycles):
  - if w=1 → B (total 3 ones, no output)
  - if w=0 → C (total 2 ones, output z!)

**Output Logic:**
- z = 1 when in state C
- z = 0 otherwise

## Key Sparkle Patterns Used
1. `Signal.loop` for state feedback
2. `hw_cond` for multi-way state transition logic
3. `Signal.register` for state storage
4. Synchronous reset via `Signal.mux`

## Generated Verilog
- 3-bit state register with synchronous reset
- Combinational next-state logic (cascaded muxes)
- Combinational output (z = state == C)
- Correctly implements the specified FSM behavior
