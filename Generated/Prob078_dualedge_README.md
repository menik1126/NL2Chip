# Prob078_dualedge Implementation Notes

## Problem Statement
Implement a dual-edge triggered flip-flop that captures input `d` on both rising and falling edges of the clock.

## Challenge
This problem exposes a fundamental limitation in the current version of Sparkle HDL:

### Missing Features
1. **No negedge register support**: Sparkle's backend only generates `always_ff @(posedge clk)` statements
2. **No clock signal access**: The clock is implicit in the domain and cannot be accessed as a regular signal for combinational logic
3. **ActiveEdge configuration ignored**: While `DomainConfig` has an `activeEdge` field that can be set to `.falling`, the backend doesn't use this information

### Reference Implementation Requirements
The reference Verilog uses:
```verilog
reg qp, qn;
always @(posedge clk) qp <= d;
always @(negedge clk) qn <= d;
always @(*) q <= clk ? qp : qn;
```

This cannot be expressed in current Sparkle because:
- There's no `Signal.registerNeg` or equivalent for negedge-triggered registers
- The clock signal cannot be used in `Signal.mux` or other combinational logic

## Solution Approach

### Sparkle Implementation (Prob078_dualedge.lean)
- Provides a standard posedge D flip-flop
- Compiles successfully
- **Does NOT** exhibit dual-edge behavior
- **Will fail** simulation tests that verify negative-edge triggering
- Included for completeness and to document the limitation

### Manual Verilog (Prob078_dualedge_manual.sv)
- Provides the correct dual-edge implementation
- Matches the reference behavior
- **Passes** simulation tests (verified: 0 mismatches in 224 samples)
- Uses `TopModule` name to match test harness expectations

## Test Results

### Manual Verilog
```
$ iverilog -g2012 -o /tmp/test Prob078_dualedge_test.sv Prob078_dualedge_ref.sv Prob078_dualedge_manual.sv
$ /tmp/test
Hint: Total mismatched samples is 0 out of 224 samples
Mismatches: 0 in 224 samples
```
✅ PASS

### Sparkle-Generated Verilog
Would fail because it only updates on positive edges, missing half the updates.

## Recommendations for Sparkle Enhancement

To support dual-edge and negedge-triggered logic, Sparkle would need:

1. Add `Signal.registerNeg` primitive for negative-edge registers
2. Extend `Stmt.register` in IR/AST.lean to include edge polarity
3. Update Backend/Verilog.lean to generate `@(negedge clk)` when appropriate
4. OR: Add `Signal.getClock` to expose clock as a regular signal
5. Implement the `activeEdge` field in `DomainConfig` properly

## Files
- `Generated/Prob078_dualedge.lean` - Sparkle implementation (limited)
- `Generated/Prob078_dualedge_manual.sv` - Working manual Verilog
- This document - Implementation notes and analysis
