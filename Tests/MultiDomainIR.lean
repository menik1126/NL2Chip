import Sparkle.Backend.Verilog
import Sparkle.Compiler.DRC

namespace Tests.MultiDomainIR

open Sparkle.IR.AST
open Sparkle.IR.Type
open Sparkle.Backend.Verilog
open Sparkle.Compiler.DRC

def writeDomain : ClockDomain :=
  { id := "write"
  , clock := "wr_clk"
  , reset := some "wr_rst"
  , periodPs := 8000
  , activeEdge := .rising
  , resetKind := .asynchronous
  }

def readDomain : ClockDomain :=
  { id := "read"
  , clock := "rd_clk"
  , reset := some "rd_rst"
  , periodPs := 12000
  , activeEdge := .falling
  , resetKind := .synchronous
  }

def testModule : Module :=
  { name := "multi_domain_registers"
  , inputs :=
      [ { name := "wr_clk", ty := .bit }
      , { name := "wr_rst", ty := .bit }
      , { name := "rd_clk", ty := .bit }
      , { name := "rd_rst", ty := .bit }
      , { name := "wr_data", ty := .bitVector 8, domain := some "write" }
      , { name := "rd_data", ty := .bitVector 8, domain := some "read" }
      ]
  , outputs := []
  , wires :=
      [ { name := "wr_q", ty := .bitVector 8 }
      , { name := "rd_q", ty := .bitVector 8 }
      , { name := "rd_free_running_q", ty := .bitVector 8 }
      ]
  , body :=
      [ .register "wr_q" "write" .domain (.ref "wr_data") (.const 0 8)
      , .register "rd_q" "read" .domain (.ref "rd_data") (.const 0 8)
      , .register "rd_free_running_q" "read" .none (.ref "rd_data") (.const 0 8)
      ]
  , clockDomains := [writeDomain, readDomain]
  }

def generated : String := toVerilog testModule

#guard generated.contains "always_ff @(posedge wr_clk or posedge wr_rst)"
#guard generated.contains "always_ff @(negedge rd_clk)"
#guard generated.contains "if (rd_rst)"
#guard !generated.contains "rd_clk__neg"
#guard checkClockDomains testModule == []

def missingDomainModule : Module :=
  { testModule with
    body := [.register "wr_q" "missing" .domain (.ref "wr_data") (.const 0 8)]
  }

#guard (checkClockDomains missingDomainModule).any (fun issue => issue.contains "unknown domain 'missing'")

def directCrossingModule : Module :=
  { testModule with
    body := [.register "rd_q" "read" .domain (.ref "wr_data") (.const 0 8)]
  }

#guard (checkClockDomains directCrossingModule).any
  (fun issue => issue.contains "register 'rd_q' input" && issue.contains "without an explicit CDC")

def mixedCombinationalModule : Module :=
  { testModule with
    wires := testModule.wires ++ [{ name := "mixed", ty := .bitVector 8 }]
    body := [.assign "mixed" (.op .xor [.ref "wr_data", .ref "rd_data"])]
  }

#guard (checkClockDomains mixedCombinationalModule).any
  (fun issue => issue.contains "assignment 'mixed' combines domains")

def explicitCrossingModule : Module :=
  { testModule with
    wires := testModule.wires ++
      [{ name := "wr_to_rd", ty := .bitVector 8, domain := some "read" }]
    body :=
      [ .cdc "wr_to_rd" "write" "read" (.ref "wr_data") .level
      , .register "rd_q" "read" .domain (.ref "wr_to_rd") (.const 0 8)
      ]
  }

#guard checkClockDomains explicitCrossingModule == []

def asyncMemoryModule : Module :=
  { testModule with
    name := "multi_domain_async_memory"
    inputs := testModule.inputs ++
      [ { name := "wr_addr", ty := .bitVector 3, domain := some "write" }
      , { name := "wr_enable", ty := .bit, domain := some "write" }
      , { name := "rd_addr", ty := .bitVector 3, domain := some "read" }
      ]
    wires := testModule.wires ++
      [{ name := "storage_data", ty := .bitVector 8, domain := some "read" }]
    body :=
      [ .asyncMemory "dual_domain_storage" (.literal 3) (.literal 8)
          "write" (.ref "wr_addr") (.ref "wr_data") (.ref "wr_enable")
          "read" (.ref "rd_addr") "storage_data"
      ]
  }

#guard checkClockDomains asyncMemoryModule == []

def asyncMemoryVerilog : String := toVerilog asyncMemoryModule

#guard asyncMemoryVerilog.contains "logic [7:0] dual_domain_storage [0:7]"
#guard asyncMemoryVerilog.contains "assign storage_data = dual_domain_storage[rd_addr]"
#guard asyncMemoryVerilog.contains "always_ff @(posedge wr_clk)"

def sameDomainAsyncMemoryModule : Module :=
  { asyncMemoryModule with
    body :=
      [ .asyncMemory "dual_domain_storage" (.literal 3) (.literal 8)
          "write" (.ref "wr_addr") (.ref "wr_data") (.ref "wr_enable")
          "write" (.ref "wr_addr") "storage_data"
      ]
  }

#guard (checkClockDomains sameDomainAsyncMemoryModule).any
  (fun issue => issue.contains "uses the same write/read domain")

def invalidAsyncMemoryFlowModule : Module :=
  { asyncMemoryModule with
    body :=
      [ .asyncMemory "dual_domain_storage" (.literal 3) (.literal 8)
          "write" (.ref "wr_addr") (.ref "rd_data") (.ref "wr_enable")
          "read" (.ref "rd_addr") "storage_data"
      ]
  }

#guard (checkClockDomains invalidAsyncMemoryFlowModule).any
  (fun issue => issue.contains "write data" && issue.contains "belongs to domain 'write'")

def hierarchyConnections : List (String × Expr) :=
  [ ("wr_clk", .ref "wr_clk")
  , ("wr_rst", .ref "wr_rst")
  , ("rd_clk", .ref "rd_clk")
  , ("rd_rst", .ref "rd_rst")
  , ("wr_data", .ref "wr_data")
  , ("rd_data", .ref "rd_data")
  ]

def hierarchyChild : Module := { testModule with name := "domain_child" }

def hierarchyParent (domainMap : List (DomainId × DomainId)) : Module :=
  { testModule with
    name := "domain_parent"
    body := [.inst "domain_child" "child" hierarchyConnections [] domainMap]
  }

def validHierarchyDesign : Design :=
  { topModule := "domain_parent"
  , modules :=
      [ hierarchyChild
      , hierarchyParent [("write", "write"), ("read", "read")]
      ]
  }

#guard checkDesignClockDomains validHierarchyDesign == []

def missingHierarchyMapDesign : Design :=
  { topModule := "domain_parent"
  , modules := [hierarchyChild, hierarchyParent []]
  }

#guard (checkDesignClockDomains missingHierarchyMapDesign).any
  (fun issue => issue.contains "has no parent mapping for child domain")

end Tests.MultiDomainIR
