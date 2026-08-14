/-
  SystemVerilog AST — Synthesizable RTL Subset

  Captures Verilog syntax faithfully before semantic lowering to Sparkle IR.
  Separate from Sparkle.IR.AST to keep a clean parser/compiler boundary.

  Supports: module with parameters, input/output/output reg, wire/reg,
  assign, always @(posedge)/always @*, if/else, case/casez,
  localparam, integer, for loops, generate if/endgenerate.
-/

import Sparkle.IR.Type

open Sparkle.IR.Type

namespace Tools.SVParser.AST

/-- Verilog numeric literal with optional width and base -/
inductive SVLiteral where
  | decimal (width : Option Nat) (value : Nat)
  | hex     (width : Option Nat) (value : Nat)
  | binary  (width : Option Nat) (value : Nat)
  deriving Repr, BEq

/-- Unary operators -/
inductive SVUnaryOp where
  | logNot    -- !
  | bitNot    -- ~
  | neg       -- - (unary minus)
  | reductAnd -- &x (reduction AND)
  | reductOr  -- |x (reduction OR)
  | signed    -- $signed(x)
  | unsigned  -- $unsigned(x); IR packed values are intrinsically unsigned
  deriving Repr, BEq

/-- Binary operators -/
inductive SVBinOp where
  -- Arithmetic
  | add | sub | mul | pow
  -- Bitwise
  | bitAnd | bitOr | bitXor
  -- Shift
  | shl | shr | asr
  -- Comparison
  | eq | neq | lt | le | gt | ge
  -- Logical
  | logAnd | logOr
  deriving Repr, BEq

/-- Expressions -/
inductive SVExpr where
  | lit     (l : SVLiteral)
  | ident   (name : String)
  | unary   (op : SVUnaryOp) (arg : SVExpr)
  | binary  (op : SVBinOp) (lhs rhs : SVExpr)
  | ternary (cond then_ else_ : SVExpr)
  | index   (arr : SVExpr) (idx : SVExpr)
  /-- Constant part-select.  Bounds remain syntax expressions until lowering so
      parameterized ranges such as `[W-1:0]` are not guessed as 32 bits. -/
  | slice   (expr : SVExpr) (hi lo : DimExpr)
  | partSelectPlus (expr : SVExpr) (base : SVExpr) (width : SVExpr)  -- [base +: width]
  | concat  (args : List SVExpr)
  | repeat_ (count : SVExpr) (value : SVExpr)  -- {n{expr}}
  | sizedCast (width : DimExpr) (value : SVExpr)
  deriving Repr, BEq

/-- Statements (inside always blocks) -/
inductive SVStmt where
  | blockAssign    (lhs rhs : SVExpr)                -- lhs = rhs;
  | nonblockAssign (lhs rhs : SVExpr)                -- lhs <= rhs;
  | ifElse (cond : SVExpr) (then_ else_ : List SVStmt)
  | caseStmt (expr : SVExpr) (arms : List (List SVExpr × List SVStmt))
      (default_ : Option (List SVStmt))
  | forLoop (init : SVStmt) (cond : SVExpr) (step : SVStmt) (body : List SVStmt)
  | assertStmt (cond : SVExpr)                          -- assert(cond);
  deriving Repr, BEq

/-- Sensitivity list for always blocks -/
inductive SVSensitivity where
  | posedge (signal : String)
  | negedge (signal : String)
  | star
  deriving Repr, BEq

/-- Port direction -/
inductive SVPortDir where
  | input | output | inout
  deriving Repr, BEq

/-- Port declaration -/
structure SVPort where
  dir    : SVPortDir
  isReg  : Bool := false            -- output reg
  isSigned : Bool := false          -- explicit `signed` declaration qualifier
  width  : Option (DimExpr × DimExpr) -- [hi:lo] or none for 1-bit
  name   : String
  deriving Repr, BEq

/-- Parameter declaration -/
structure SVParam where
  name     : String
  width    : Option (DimExpr × DimExpr) -- optional [hi:lo]
  value    : SVExpr                 -- default value expression
  isLocal  : Bool := false          -- localparam vs parameter
  isSigned : Bool := false          -- `integer` or explicit `signed`
  deriving Repr, BEq

/-- Module-level items -/
inductive SVModuleItem where
  | wireDecl      (name : String) (width : Option (DimExpr × DimExpr))
                  (initExpr : Option SVExpr)              -- wire [w] x = expr;
                  (isSigned : Bool := false)
  | regDecl       (name : String) (width : Option (DimExpr × DimExpr))
                  (arraySize : Option DimExpr)            -- reg [w] x [0:N];
                  (isSigned : Bool := false)
  | integerDecl   (name : String)                         -- integer i;
  | paramDecl     (param : SVParam)                       -- parameter/localparam
  | contAssign    (lhs rhs : SVExpr)                      -- assign lhs = rhs;
  | alwaysBlock   (sensitivity : SVSensitivity) (body : List SVStmt)
  | generateBlock (cond : SVExpr) (body : List SVModuleItem)
                  (elseBody : List SVModuleItem)          -- generate if (...) ... endgenerate
  | instantiation (moduleName instName : String)
                  (connections : List (String × SVExpr))
                  (paramOverrides : List (String × SVExpr) := [])
  | taskDecl      (name : String) (body : List SVStmt)    -- task ... endtask
  | readmemh      (filename : String) (memName : String)  -- $readmemh("file", mem)
  /-- Sparkle emits elaboration-time validation guards.  They are represented
      explicitly so lowering can discard only these canonical diagnostics
      while continuing to reject ordinary parameter-dependent generate logic. -/
  | validationGuard (cond : SVExpr)
  deriving Repr, BEq

/-- A parsed Verilog module -/
structure SVModule where
  name   : String
  params : List SVParam := []       -- #(parameter ...) list
  ports  : List SVPort
  items  : List SVModuleItem
  deriving Repr, BEq

/-- A collection of modules -/
structure SVDesign where
  modules : List SVModule
  deriving Repr, BEq

end Tools.SVParser.AST
