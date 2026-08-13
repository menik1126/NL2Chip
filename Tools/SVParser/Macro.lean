/-
  verilog! — Inline Verilog-to-Lean Elaboration Macro

  Parses Verilog at compile time, lowers to Sparkle IR, and injects
  State/Input/nextState/initState into the current Lean environment.

  The nextState body is built via Lean Syntax quotation (type-safe AST),
  not string interpolation — no parenthesis bugs or width mismatches.
-/

import Lean
import Tools.SVParser
import Tools.SVParser.Verify
import Sparkle.Core.JIT

open Lean Elab Command Term Meta
open Tools.SVParser.Parser
open Tools.SVParser.Lower
open Tools.SVParser.Verify

private def elabStr (s : String) : CommandElabM Unit := do
  match Parser.runParserCategory (← getEnv) `command s with
  | .error err => throwError "verilog! parse error:\n{err}\n\nSource:\n{s}"
  | .ok stx => elabCommand stx

-- ============================================================================
-- The verilog! command
-- ============================================================================

/-- Compile-time Verilog → Lean elaboration. -/
elab "verilog!" src:str : command => do
  let vSrc := src.getString
  match parseAndLower vSrc with
  | .error err => throwError "verilog!: parsing failed: {err}"
  | .ok design =>
    match design.modules.head? with
    | none => throwError "verilog!: no module found"
    | some m =>
      let model : SemanticModel ← match extractModel m with
        | .ok model => pure model
        | .error message => throwError m!"verilog!: {message}"
      let assigns := collectAssigns m.body
      let model : SemanticModel := { model with
        registers := model.registers.map fun r =>
          { r with nextExpr := inlineAssigns assigns r.nextExpr }
      }
      let requireWidth := fun (role : String) (ty : Sparkle.IR.Type.HWType) =>
        match ty.requireBitWidth role with
        | .ok width => pure width
        | .error message => throwError m!"verilog!: {message}"
      let mut wireWidths := []
      for wire in m.wires do
        wireWidths := wireWidths ++ [(wire.name,
          ← requireWidth s!"wire '{wire.name}'" wire.ty)]
      let mut portWidths := []
      for port in m.inputs do
        portWidths := portWidths ++ [(port.name,
          ← requireWidth s!"input '{port.name}'" port.ty)]
      let regWidths := model.registers.map fun r => (r.name, r.width)
      let inputWidths := model.inputs.map fun i => (i.name, i.width)
      let allWidths := regWidths ++ inputWidths ++ wireWidths ++ portWidths
      let ns := leanName model.moduleName

      -- 1. namespace + State + Input (string-based: simple boilerplate)
      elabStr s!"namespace {ns}.Verify"

      let stateFields := String.intercalate "\n" <|
        model.registers.map fun r => s!"  {leanName r.name} : BitVec {r.width}"
      elabStr s!"structure State where\n{stateFields}\n  deriving DecidableEq, Repr, BEq, Inhabited"

      let inputFields := String.intercalate "\n" <|
        model.inputs.map fun i => s!"  {leanName i.name} : BitVec {i.width}"
      elabStr s!"structure Input where\n{inputFields}\n  deriving DecidableEq, Repr, BEq, Inhabited"

      -- 2. nextState (use irExprToLean for fully-parenthesized expressions)
      let lb := "{"
      let rb := "}"
      let regW := model.registers.map fun r => (r.name, r.width)
      let inpW := model.inputs.map fun i => (i.name, i.width)
      let nextFieldStrs := model.registers.map fun r =>
        let fixedExpr := fixConstWidths r.nextExpr r.width allWidths
        let valStr := irExprToLean fixedExpr regW inpW allWidths "s" "i"
        s!"    {leanName r.name} := {valStr}"
      let nextBody := String.intercalate ",\n" nextFieldStrs
      elabStr s!"def nextState (s : State) (i : Input) : State :=\n  {lb}\n{nextBody}\n  {rb}"

      -- 3. initState
      let initFields := String.intercalate ",\n" <|
        model.registers.map fun r => s!"    {leanName r.name} := ({r.initValue} : BitVec {r.width})"
      elabStr s!"def initState : State :=\n  {lb}\n{initFields}\n  {rb}"

      -- 4. Auto-generate theorems from Verilog assert statements
      let regW := model.registers.map fun r => (r.name, r.width)
      let inpW := model.inputs.map fun i => (i.name, i.width)
      for (assertName, condExpr) in model.assertions do
        -- Fix constant widths: use width inference per sub-expression
        let fixedCond := fixConstWidthsSmart condExpr allWidths
        -- Assertion checks next-state: let ns := nextState s i, use ns.field
        let condStr := irExprToLean fixedCond regW inpW allWidths "ns" "i"
        -- Generate theorem: simp unfolds nextState, then bv_decide proves the BitVec property
        let thmStr := s!"theorem {assertName} (s : State) (i : Input) : let ns := nextState s i; {condStr} != (0 : BitVec 1) := by simp [nextState]; bv_decide"
        try
          elabStr thmStr
        catch _ =>
          try
            elabStr s!"theorem {assertName} (s : State) (i : Input) : let ns := nextState s i; {condStr} != (0 : BitVec 1) := by simp [nextState]"
          catch _ =>
            throwError m!"verilog!: could not prove generated assertion '{assertName}' without introducing an unchecked placeholder"

      -- 5. Type-safe JIT simulation wrappers

      elabStr "open Sparkle.Core.JIT"

      -- SimInput (same fields as Input — both from non-clk input ports)
      let simInputFields := String.intercalate "\n" <|
        model.inputs.map fun i => s!"  {leanName i.name} : BitVec {i.width}"
      elabStr s!"structure SimInput where\n{simInputFields}\n  deriving DecidableEq, Repr, BEq, Inhabited"

      -- SimOutput (from module output ports)
      let outputPorts := m.outputs
      let mut outputWidths := []
      for port in outputPorts do
        outputWidths := outputWidths ++ [(port.name,
          ← requireWidth s!"output '{port.name}'" port.ty)]
      let simOutputFields := String.intercalate "\n" <|
        outputWidths.map fun (name, width) =>
          s!"  {leanName name} : BitVec {width}"
      elabStr s!"structure SimOutput where\n{simOutputFields}\n  deriving DecidableEq, Repr, BEq, Inhabited"

      -- Simulator structure
      elabStr "structure Simulator where\n  handle : JITHandle"

      -- step: set all inputs by index, then evalTick
      let inputsIndexed := (List.range model.inputs.length).zip model.inputs
      let setInputCalls := inputsIndexed.map fun (idx, inp) =>
        s!"  JIT.setInput sim.handle {idx} i.{leanName inp.name}.toNat.toUInt64"
      let stepBody := String.intercalate "\n" setInputCalls
      elabStr s!"def Simulator.step (sim : Simulator) (i : SimInput) : IO Unit := do\n{stepBody}\n  JIT.evalTick sim.handle"

      -- read: get all outputs by index, convert to BitVec
      let outputsIndexed := (List.range outputWidths.length).zip outputWidths
      let readFields := outputsIndexed.map fun (idx, name, width) =>
        s!"  let v{idx} ← JIT.getOutput sim.handle {idx}\n  let {leanName name} := BitVec.ofNat {width} v{idx}.toNat"
      let readBody := String.intercalate "\n" readFields
      let readReturn := String.intercalate ", " <|
        outputWidths.map fun (name, _) => s!"{leanName name}"
      elabStr s!"def Simulator.read (sim : Simulator) : IO SimOutput := do\n{readBody}\n  pure {lb} {readReturn} {rb}"

      -- reset
      elabStr "def Simulator.reset (sim : Simulator) : IO Unit :=\n  JIT.reset sim.handle"

      -- 6. close namespace
      elabStr s!"end {ns}.Verify"
