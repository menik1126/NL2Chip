/-
  CKTLean HDL - Root Module

  A functional hardware description language in Lean 4.
  Inspired by Haskell's Clash, designed for type-safe hardware design.
-/

import cktlean.Core.Domain
import cktlean.Core.Signal
import cktlean.Core.Circuit
import cktlean.Core.StateMacro
import cktlean.Core.Vector
import cktlean.Core.OptimizedSim
import cktlean.Data.BitPack
import cktlean.IR.Type
import cktlean.IR.AST
import cktlean.IR.Builder
import cktlean.IR.Optimize
import cktlean.Compiler.Elab
import cktlean.Compiler.DRC
import cktlean.Backend.Verilog
import cktlean.Backend.VCD
import cktlean.Backend.CppSim
import cktlean.Verification.Temporal
import cktlean.Core.JIT
import cktlean.Core.JITLoop
import cktlean.Core.Oracle
import cktlean.Utils.HexLoader
import cktlean.Library.RTL
