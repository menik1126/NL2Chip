/-
  Abstract Syntax Tree for Hardware Netlist

  Defines the IR (Intermediate Representation) that hardware designs compile to.
  This is similar to a simplified Verilog AST.
-/

import Sparkle.IR.Type

namespace Sparkle.IR.AST

open Sparkle.IR.Type

/-- Stable identifier for a clock domain within a module. -/
abbrev DomainId := String

/-- Active clock edge used by a hardware clock domain. -/
inductive ClockEdge where
  | rising
  | falling
  deriving Repr, BEq, DecidableEq, Inhabited

/-- Reset timing relative to the domain clock. -/
inductive ResetKind where
  | synchronous
  | asynchronous
  deriving Repr, BEq, DecidableEq, Inhabited

/--
  A physical clock domain carried by the netlist IR.

  `id` is the identity used by sequential statements. `clock` and `reset`
  are public module ports. Keeping these separate prevents two equal-frequency
  clocks from being collapsed into one domain.
-/
structure ClockDomain where
  id         : DomainId
  clock      : String
  reset      : Option String := some "rst"
  periodPs   : Nat := 10000
  activeEdge : ClockEdge := .rising
  resetKind  : ResetKind := .asynchronous
  deriving Repr, BEq, Inhabited

namespace ClockDomain

/-- Legacy single-clock domain used by compatibility builder APIs. -/
def legacy (clock reset : String) : ClockDomain :=
  { id := clock
  , clock := clock
  , reset := some reset
  , resetKind := .asynchronous
  }

end ClockDomain

/-- Whether a register uses its domain reset or deliberately has no reset. -/
inductive RegisterReset where
  | domain
  | none
  deriving Repr, BEq, DecidableEq, Inhabited

instance : ToString RegisterReset where
  toString
    | .domain => "domain"
    | .none => "none"

/-- Audited kinds of intentional clock-domain crossing. -/
inductive CdcKind where
  | level
  | pulse
  | asyncFifo
  deriving Repr, BEq, DecidableEq, Inhabited

instance : ToString CdcKind where
  toString
    | .level => "level"
    | .pulse => "pulse"
    | .asyncFifo => "async_fifo"

/-- Port declaration (input/output of a module) -/
structure Port where
  name   : String
  ty     : HWType
  domain : Option DomainId := none
  deriving Repr, BEq, Inhabited


/--
  Hardware operators

  These represent primitive operations that map directly to hardware gates.
-/
inductive Operator where
  | and  : Operator  -- Bitwise AND
  | or   : Operator  -- Bitwise OR
  | xor  : Operator  -- Bitwise XOR
  | not  : Operator  -- Bitwise NOT
  | add  : Operator  -- Addition
  | sub  : Operator  -- Subtraction
  | mul  : Operator  -- Multiplication
  | eq   : Operator  -- Equality comparison
  | lt_u : Operator  -- Less than comparison (unsigned)
  | lt_s : Operator  -- Less than comparison (signed)
  | le_u : Operator  -- Less than or equal (unsigned)
  | le_s : Operator  -- Less than or equal (signed)
  | gt_u : Operator  -- Greater than (unsigned)
  | gt_s : Operator  -- Greater than (signed)
  | ge_u : Operator  -- Greater than or equal (unsigned)
  | ge_s : Operator  -- Greater than or equal (signed)
  | mux  : Operator  -- Multiplexer (ternary: condition ? then : else)
  | shl  : Operator  -- Shift left
  | shr  : Operator  -- Shift right (logical)
  | asr  : Operator  -- Arithmetic shift right (signed)
  | neg  : Operator  -- Arithmetic negation
  deriving Repr, BEq, DecidableEq

namespace Operator

/-- Convert operator to string representation -/
def toString : Operator → String
  | and  => "and"
  | or   => "or"
  | xor  => "xor"
  | not  => "not"
  | add  => "add"
  | sub  => "sub"
  | mul  => "mul"
  | eq   => "eq"
  | lt_u => "lt_u"
  | lt_s => "lt_s"
  | le_u => "le_u"
  | le_s => "le_s"
  | gt_u => "gt_u"
  | gt_s => "gt_s"
  | ge_u => "ge_u"
  | ge_s => "ge_s"
  | mux  => "mux"
  | shl  => "shl"
  | shr  => "shr"
  | asr  => "asr"
  | neg  => "neg"

instance : ToString Operator where
  toString := Operator.toString

end Operator

/--
  Expression in the netlist IR

  - Const: Literal constant value
  - Ref: Reference to a wire or port by name
  - Op: Application of an operator to arguments
-/
inductive Expr where
  | const (value : Int) (width : Nat) : Expr
  | ref (name : String) : Expr
  | op (operator : Operator) (args : List Expr) : Expr
  | concat (args : List Expr) : Expr
  | slice (expr : Expr) (hi lo : Nat) : Expr
  | index (array : Expr) (idx : Expr) : Expr
  deriving Repr, BEq, Inhabited

namespace Expr

/-- Create a constant expression from a BitVec -/
def ofBitVec {n : Nat} (bv : BitVec n) : Expr :=
  .const bv.toInt n

/-- Create a wire reference -/
def wire (name : String) : Expr := .ref name

/-- Helper constructors for common operations -/
def and (a b : Expr) : Expr := .op .and [a, b]
def or (a b : Expr) : Expr := .op .or [a, b]
def xor (a b : Expr) : Expr := .op .xor [a, b]
def not (a : Expr) : Expr := .op .not [a]
def add (a b : Expr) : Expr := .op .add [a, b]
def sub (a b : Expr) : Expr := .op .sub [a, b]
def mul (a b : Expr) : Expr := .op .mul [a, b]
def eq (a b : Expr) : Expr := .op .eq [a, b]
def lt_u (a b : Expr) : Expr := .op .lt_u [a, b]
def lt_s (a b : Expr) : Expr := .op .lt_s [a, b]
def mux (cond then_ else_ : Expr) : Expr := .op .mux [cond, then_, else_]

/-- Convert expression to string (for debugging) -/
partial def toString : Expr → String
  | const v w => s!"{v}#{w}"
  | ref name => name
  | op operator args =>
      let argStr := String.intercalate ", " (args.map toString)
      s!"{operator}({argStr})"
  | concat args => s!"\{{String.intercalate ", " (args.map toString)}}"
  | slice e hi lo => s!"{toString e}[{hi}:{lo}]"
  | index arr idx => s!"{toString arr}[{toString idx}]"

instance : ToString Expr where
  toString := Expr.toString

end Expr

/--
  Statement in the netlist IR

  - Assign: Continuous assignment (combinational logic)
  - Register: Sequential logic (D flip-flop)
  - Memory: Synchronous RAM/BRAM primitive
  - Inst: Instantiation of another module
-/
inductive Stmt where
  | assign (lhs : String) (rhs : Expr) : Stmt
  | cdc
      (output : String)
      (sourceDomain : DomainId)
      (destDomain : DomainId)
      (input : Expr)
      (kind : CdcKind)
      : Stmt
  | register
      (output : String)      -- Output wire name
      (domain : DomainId)    -- Owning clock domain
      (reset : RegisterReset)-- Reset behavior for this register
      (input : Expr)         -- Input expression
      (initValue : Int)      -- Reset/initial value
      : Stmt
  | memory
      (name : String)         -- Memory instance name
      (addrWidth : Nat)       -- Address width (size = 2^addrWidth)
      (dataWidth : Nat)       -- Data width
      (domain : DomainId)     -- Owning clock domain
      (writeAddr : Expr)      -- Write address port
      (writeData : Expr)      -- Write data port
      (writeEnable : Expr)    -- Write enable port
      (readAddr : Expr)       -- Read address port
      (readData : String)     -- Read data output wire
      (comboRead : Bool := false) -- Combinational (same-cycle) read
      : Stmt
  | inst
      (moduleName : String)   -- Name of module to instantiate
      (instName : String)     -- Instance name
      (connections : List (String × Expr))  -- Port connections
      (domainMap : List (DomainId × DomainId) := []) -- Child domain -> parent domain
      : Stmt
  deriving Repr, BEq

namespace Stmt

/-- Convert statement to string (for debugging) -/
def toString : Stmt → String
  | assign lhs rhs => s!"{lhs} := {rhs}"
  | cdc output sourceDomain destDomain input kind =>
      s!"cdc[{kind}] {output}: {sourceDomain} -> {destDomain} <= {input}"
  | register output domain reset input initValue =>
      s!"reg {output} @domain({domain}, reset={reset}) <= {input} (init: {initValue})"
  | memory name addrWidth dataWidth domain writeAddr writeData writeEnable readAddr readData comboRead =>
      let readKind := if comboRead then "combo_read" else "read"
      s!"memory {name}[2^{addrWidth}][{dataWidth}] @domain({domain}) " ++
      s!"write({writeAddr}, {writeData}, {writeEnable}) {readKind}({readAddr}) => {readData}"
  | inst modName instName conns domainMap =>
      let connStr := String.intercalate ", " (conns.map fun (p, e) => s!".{p}({e})")
      let domainStr := String.intercalate ", "
        (domainMap.map fun (child, parent) => s!"{child}->{parent}")
      s!"{modName} {instName}({connStr}) domains[{domainStr}]"

instance : ToString Stmt where
  toString := Stmt.toString

end Stmt

/--
  Module: A hardware module with inputs, outputs, internal wires, and logic

  This represents a synthesizable hardware component.

  If isPrimitive is true, this is a blackbox module (e.g., vendor SRAM, clock gate)
  that should be instantiated but not defined. The body is ignored for primitives.
-/
structure Module where
  name        : String
  inputs      : List Port
  outputs     : List Port
  wires       : List Port    -- Internal wires (ignored for primitives)
  body        : List Stmt    -- Logic (ignored for primitives)
  clockDomains : List ClockDomain := [] -- Physical clock/reset domains
  assertions  : List (String × Expr) := []  -- Formal assertions (name, condition)
  isPrimitive : Bool := false  -- True for vendor-provided blackbox modules
  deriving Repr, BEq

namespace Module

/-- Create an empty module -/
def empty (name : String) : Module :=
  { name := name
  , inputs := []
  , outputs := []
  , wires := []
  , body := []
  , clockDomains := []
  , isPrimitive := false
  }

/-- Create a primitive (blackbox) module with specified interface -/
def primitive (name : String) (inputs : List Port) (outputs : List Port) : Module :=
  { name := name
  , inputs := inputs
  , outputs := outputs
  , wires := []
  , body := []
  , clockDomains := []
  , isPrimitive := true
  }

/-- Add an input port -/
def addInput (m : Module) (p : Port) : Module :=
  { m with inputs := m.inputs ++ [p] }

/-- Add an output port -/
def addOutput (m : Module) (p : Port) : Module :=
  { m with outputs := m.outputs ++ [p] }

/-- Add an internal wire -/
def addWire (m : Module) (p : Port) : Module :=
  { m with wires := m.wires ++ [p] }

/-- Add a physical clock domain once. Conflicting duplicates are rejected by DRC. -/
def addClockDomain (m : Module) (domain : ClockDomain) : Module :=
  if m.clockDomains.any (fun existing => existing == domain) then m
  else { m with clockDomains := m.clockDomains ++ [domain] }

/-- Resolve a clock domain by its stable IR identifier. -/
def findClockDomain? (m : Module) (id : DomainId) : Option ClockDomain :=
  m.clockDomains.find? (fun domain => domain.id == id)

/-- Add a statement to the body -/
def addStmt (m : Module) (s : Stmt) : Module :=
  { m with body := m.body ++ [s] }

/-- Convert module to string (for debugging) -/
def toString (m : Module) : String :=
  let inputStr := String.intercalate ", " (m.inputs.map fun p => s!"{p.name}: {p.ty}")
  let outputStr := String.intercalate ", " (m.outputs.map fun p => s!"{p.name}: {p.ty}")
  let wireStr := String.intercalate ", " (m.wires.map fun p => s!"{p.name}: {p.ty}")
  let domainStr := String.intercalate ", " (m.clockDomains.map fun d => s!"{d.id}:{d.clock}")
  let bodyStr := String.intercalate "\n  " (m.body.map Stmt.toString)
  s!"module {m.name}\n" ++
  s!"  domains: {domainStr}\n" ++
  s!"  inputs:  {inputStr}\n" ++
  s!"  outputs: {outputStr}\n" ++
  s!"  wires:   {wireStr}\n" ++
  s!"  body:\n  {bodyStr}"

instance : ToString Module where
  toString := Module.toString

end Module

/--
  Design: A collection of modules that make up a hardware project.
-/
structure Design where
  topModule : String
  modules   : List Module
  deriving Repr, BEq

namespace Design

/-- Create an empty design -/
def empty (topName : String) : Design :=
  { topModule := topName, modules := [] }

/-- Add a module to the design -/
def addModule (d : Design) (m : Module) : Design :=
  { d with modules := d.modules ++ [m] }

/-- Find a module by name -/
def findModule (d : Design) (name : String) : Option Module :=
  d.modules.find? (·.name == name)

end Design

end Sparkle.IR.AST
