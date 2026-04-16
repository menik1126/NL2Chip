#!/usr/bin/env python3

# Generate Sparkle HDL code for Conway's Game of Life
# Break into smaller chunks to avoid compilation issues

def get_neighbors(row, col):
    """Return list of (row, col) tuples for 8 neighbors with toroidal wrapping"""
    r_up = (row - 1) % 16
    r_dn = (row + 1) % 16
    c_lt = (col - 1) % 16
    c_rt = (col + 1) % 16
    
    return [
        (r_up, c_lt), (r_up, col), (r_up, c_rt),
        (row, c_lt),               (row, c_rt),
        (r_dn, c_lt), (r_dn, col), (r_dn, c_rt)
    ]

def bit_index(row, col):
    return row * 16 + col

print("""import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option maxRecDepth 10000
set_option maxHeartbeats 2000000

/-- Compute next generation for all 256 cells inline -/
def nextGrid (grid : BitVec 256) : BitVec 256 :=""")

# Generate computation for each cell
for cell_idx in range(256):
    row = cell_idx // 16
    col = cell_idx % 16
    
    # Get current cell
    curr_idx = bit_index(row, col)
    
    # Get all 8 neighbors
    neighbors = get_neighbors(row, col)
    
    # Extract neighbor bits
    neighbor_exprs = []
    for nr, nc in neighbors:
        nidx = bit_index(nr, nc)
        neighbor_exprs.append(f"((grid >>> {nidx}) &&& 1#256)")
    
    # Sum neighbors (extend to 4 bits)
    sum_expr = " + ".join([f"({ne}.zeroExtend 4)" for ne in neighbor_exprs])
    
    # Get current cell state
    curr_expr = f"((grid >>> {curr_idx}) &&& 1#256)"
    
    print(f"  let count{cell_idx} := {sum_expr}")
    print(f"  let alive{cell_idx} := {curr_expr}")
    print(f"  let combined{cell_idx} := (count{cell_idx}.zeroExtend 8) ||| (alive{cell_idx}.zeroExtend 8)")
    print(f"  let next{cell_idx} := if combined{cell_idx} == 3#8 then 1#1 else 0#1")

# Pack all bits into result - do it in chunks of 16 (one row at a time)
print("  -- Pack all bits into result (by rows)")
for row in range(16):
    start_idx = row * 16
    end_idx = start_idx + 16
    parts = []
    for idx in range(start_idx, end_idx):
        if idx == start_idx:
            parts.append(f"(next{idx}.zeroExtend 16)")
        else:
            offset = idx - start_idx
            parts.append(f"((next{idx}.zeroExtend 16) <<< {offset})")
    print(f"  let row{row} := {' ||| '.join(parts)}")

# Combine all rows
print("  -- Combine all rows")
print("  let result :=")
for row in range(16):
    if row == 0:
        print(f"    (row{row}.zeroExtend 256) |||")
    elif row == 15:
        print(f"    ((row{row}.zeroExtend 256) <<< {row * 16})")
    else:
        print(f"    ((row{row}.zeroExtend 256) <<< {row * 16}) |||")

print("""  result

/-- Conway's Game of Life on a 16x16 toroidal grid -/
def prob144_conwaylife {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 256))
    : Signal dom (BitVec 256) :=
  Signal.loop fun (q : Signal dom (BitVec 256)) =>
    let next := Signal.map nextGrid q
    let nextVal := Signal.mux load data next
    Signal.register 0#256 nextVal

#synthesizeVerilog prob144_conwaylife
""")
