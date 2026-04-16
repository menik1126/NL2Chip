# Prob058_alwaysblock2: XOR Gate Three Ways

## Solution Overview

This problem requires implementing an XOR gate in three different ways:
1. **out_assign**: Combinational logic (assign statement)
2. **out_always_comb**: Combinational logic (always @(*) block)
3. **out_always_ff**: Sequential logic (always @(posedge clk) block)

## Sparkle HDL Implementation

The Sparkle implementation produces a single XOR computation and reuses it for both combinational outputs, with the third output being the registered version:

```lean
let xor_result := a ^^^ b
let out_always_ff := Signal.register 0#1 xor_result
bundle2 xor_result (bundle2 xor_result out_always_ff)
```

## Output Packing

Sparkle HDL packs tuple outputs into a single port `out[2:0]`:
- `out[2]` = out_assign (combinational XOR)
- `out[1]` = out_always_comb (combinational XOR)
- `out[0]` = out_always_ff (registered XOR)

## Wrapper Module

The wrapper module `TopModule` unpacks the tuple into separate ports to match the testbench interface:
- Maps packed `out[2:0]` to three individual 1-bit outputs
- Connects clk and sets rst to 0 (no reset signal in the original spec)

## Files Generated

1. `Prob058_alwaysblock2.lean` - Sparkle HDL source
2. `Prob058_alwaysblock2.sv` - Generated SystemVerilog module
3. `Prob058_alwaysblock2_wrapper.sv` - Wrapper to match testbench interface
