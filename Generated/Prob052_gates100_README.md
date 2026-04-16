# Prob052_gates100: 100-Input Reduction Gates

## Problem Description
Implement a combinational circuit with 100 inputs (`in[99:0]`) and 3 outputs:
1. `out_and`: 100-input AND gate (all bits must be 1 for output to be 1)
2. `out_or`: 100-input OR gate (at least one bit must be 1 for output to be 1)
3. `out_xor`: 100-input XOR gate (parity of all input bits)

## Implementation Strategy
Since Sparkle doesn't have built-in reduction operators, we manually unroll the operations:
- Used `extractLsb' i 1` to extract each bit as a 1-bit `BitVec 1`
- Chained all 100 bits with the appropriate operator (`&&&`, `|||`, or `^^^`)
- Used `bundle2` to combine outputs into a tuple (Sparkle's multi-output mechanism)

## Generated Verilog Interface
```systemverilog
module prob052_gates100 (
    input logic [99:0] _gen_input,
    output logic [2:0] out
);
```

Output bit mapping:
- `out[2]` = AND reduction result
- `out[1]` = OR reduction result  
- `out[0]` = XOR reduction result

## Notes
- Purely combinational (no registers)
- Total of 300 bit extractions (100 per reduction operation)
- The synthesis creates a flat chain of operations for each reduction

