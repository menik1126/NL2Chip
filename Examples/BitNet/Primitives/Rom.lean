import cktlean.IR.Builder
import Examples.BitNet.Config

namespace cktlean.Examples.BitNet.Primitives

open cktlean.IR.Builder
open cktlean.IR.AST
open cktlean.IR.Type

def mkWeightROM (cfg : BitLinearConfig) : Module :=
  let name := s!"WeightROM_{cfg.outDim}x{cfg.inDim}"
  mkROMPrimitive name cfg.romAddrBits romWordBits

def mkScaleROM (cfg : BitLinearConfig) : Module :=
  let name := s!"ScaleROM_{cfg.outDim}"
  mkROMPrimitive name cfg.scaleAddrBits scaleTotalBits

end cktlean.Examples.BitNet.Primitives
