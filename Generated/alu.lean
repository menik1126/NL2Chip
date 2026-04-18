import Sparkle
import Sparkle.Compiler.Elab

set_option maxRecDepth 8192
set_option maxHeartbeats 800000

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Operation codes as private constants
private def ADD  : BitVec 6 := 32#6
private def ADDU : BitVec 6 := 33#6
private def SUB  : BitVec 6 := 34#6
private def SUBU : BitVec 6 := 35#6
private def AND  : BitVec 6 := 36#6
private def OR   : BitVec 6 := 37#6
private def XOR  : BitVec 6 := 38#6
private def NOR  : BitVec 6 := 39#6
private def SLT  : BitVec 6 := 42#6
private def SLTU : BitVec 6 := 43#6
private def SLL  : BitVec 6 := 0#6
private def SRL  : BitVec 6 := 2#6
private def SRA  : BitVec 6 := 3#6
private def SLLV : BitVec 6 := 4#6
private def SRLV : BitVec 6 := 6#6
private def SRAV : BitVec 6 := 7#6
private def LUI  : BitVec 6 := 15#6

/-- 32-bit MIPS ALU with 17 operations and 6 status flags.
    Returns: (r, flags) where flags[4:0] = {flag, overflow, negative, carry, zero} -/
def alu {dom : DomainConfig}
    (a b : Signal dom (BitVec 32))
    (aluc : Signal dom (BitVec 6))
    : Signal dom (BitVec 32 × BitVec 5) :=
  
  -- Compute result based on aluc using nested Signal.mux
  let isADD  := aluc === Signal.pure ADD
  let isADDU := aluc === Signal.pure ADDU
  let isSUB  := aluc === Signal.pure SUB
  let isSUBU := aluc === Signal.pure SUBU
  let isAND  := aluc === Signal.pure AND
  let isOR   := aluc === Signal.pure OR
  let isXOR  := aluc === Signal.pure XOR
  let isNOR  := aluc === Signal.pure NOR
  let isSLT  := aluc === Signal.pure SLT
  let isSLTU := aluc === Signal.pure SLTU
  let isSLL  := aluc === Signal.pure SLL
  let isSRL  := aluc === Signal.pure SRL
  let isSRA  := aluc === Signal.pure SRA
  let isSLLV := aluc === Signal.pure SLLV
  let isSRLV := aluc === Signal.pure SRLV
  let isSRAV := aluc === Signal.pure SRAV
  let isLUI  := aluc === Signal.pure LUI
  
  -- Arithmetic operations
  let add_result := a + b
  let sub_result := a - b
  
  -- Logical operations
  let and_result := a &&& b
  let or_result := a ||| b
  let xor_result := a ^^^ b
  let nor_result := ~~~(a ||| b)
  
  -- Comparison operations (SLT/SLTU)
  let slt_cond := Signal.map (fun (a_val, b_val) => a_val.toInt < b_val.toInt) (bundle2 a b)
  let sltu_cond := Signal.map (fun (a_val, b_val) => a_val < b_val) (bundle2 a b)
  let slt_result := Signal.mux slt_cond (Signal.pure 1#32) (Signal.pure 0#32)
  let sltu_result := Signal.mux sltu_cond (Signal.pure 1#32) (Signal.pure 0#32)
  
  -- Shift operations
  let sll_result := Signal.map (fun (a_val, b_val) => b_val <<< a_val.toNat) (bundle2 a b)
  let srl_result := Signal.map (fun (a_val, b_val) => b_val >>> a_val.toNat) (bundle2 a b)
  let sra_result := Signal.map (fun (a_val, b_val) => BitVec.sshiftRight b_val a_val.toNat) (bundle2 a b)
  let sllv_result := Signal.map (fun (a_val, b_val) => b_val <<< (a_val &&& 31#32).toNat) (bundle2 a b)
  let srlv_result := Signal.map (fun (a_val, b_val) => b_val >>> (a_val &&& 31#32).toNat) (bundle2 a b)
  let srav_result := Signal.map (fun (a_val, b_val) => BitVec.sshiftRight b_val (a_val &&& 31#32).toNat) (bundle2 a b)
  
  -- LUI operation
  let lui_result := Signal.map (fun a_val => (a_val &&& 0xFFFF#32) <<< 16) a
  
  -- Mux tree for result selection (nested muxes)
  let r_temp1 := Signal.mux isLUI lui_result (Signal.pure 0#32)
  let r_temp2 := Signal.mux isSRAV srav_result r_temp1
  let r_temp3 := Signal.mux isSRLV srlv_result r_temp2
  let r_temp4 := Signal.mux isSLLV sllv_result r_temp3
  let r_temp5 := Signal.mux isSRA sra_result r_temp4
  let r_temp6 := Signal.mux isSRL srl_result r_temp5
  let r_temp7 := Signal.mux isSLL sll_result r_temp6
  let r_temp8 := Signal.mux isSLTU sltu_result r_temp7
  let r_temp9 := Signal.mux isSLT slt_result r_temp8
  let r_temp10 := Signal.mux isNOR nor_result r_temp9
  let r_temp11 := Signal.mux isXOR xor_result r_temp10
  let r_temp12 := Signal.mux isOR or_result r_temp11
  let r_temp13 := Signal.mux isAND and_result r_temp12
  let r_temp14 := Signal.mux isSUBU sub_result r_temp13
  let r_temp15 := Signal.mux isSUB sub_result r_temp14
  let r_temp16 := Signal.mux isADDU add_result r_temp15
  let r := Signal.mux isADD add_result r_temp16
  
  -- Zero flag: result is zero
  let zero_cond := r === Signal.pure 0#32
  let zero := Signal.mux zero_cond (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Carry flag: for ADD/ADDU/SUB/SUBU operations
  let add_carry_cond := Signal.map (fun (a_val, b_val) =>
    let sum := a_val.toNat + b_val.toNat
    sum >= 2^32
  ) (bundle2 a b)
  let add_carry := Signal.mux add_carry_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let sub_carry_cond := Signal.map (fun (a_val, b_val) => a_val < b_val) (bundle2 a b)
  let sub_carry := Signal.mux sub_carry_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let is_add_op := isADD ||| isADDU
  let is_sub_op := isSUB ||| isSUBU
  let carry_temp := Signal.mux is_sub_op sub_carry (Signal.pure 0#1)
  let carry := Signal.mux is_add_op add_carry carry_temp
  
  -- Negative flag: MSB of result (bit 31)
  let negative_cond := Signal.map (fun r_val => r_val.getLsb 31) r
  let negative := Signal.mux negative_cond (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Overflow flag: for signed ADD/SUB
  let add_overflow_cond := Signal.map (fun ((a_val, b_val), r_val) =>
    let a_sign := a_val.getLsb 31
    let b_sign := b_val.getLsb 31
    let r_sign := r_val.getLsb 31
    a_sign == b_sign && a_sign != r_sign
  ) (bundle2 (bundle2 a b) r)
  let add_overflow := Signal.mux add_overflow_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let sub_overflow_cond := Signal.map (fun ((a_val, b_val), r_val) =>
    let a_sign := a_val.getLsb 31
    let b_sign := b_val.getLsb 31
    let r_sign := r_val.getLsb 31
    a_sign != b_sign && a_sign != r_sign
  ) (bundle2 (bundle2 a b) r)
  let sub_overflow := Signal.mux sub_overflow_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let overflow_temp := Signal.mux isSUB sub_overflow (Signal.pure 0#1)
  let overflow := Signal.mux isADD add_overflow overflow_temp
  
  -- Flag: for SLT/SLTU
  let flag_temp1 := Signal.mux sltu_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let flag_temp2 := Signal.mux isSLTU flag_temp1 (Signal.pure 0#1)
  let flag_temp3 := Signal.mux slt_cond (Signal.pure 1#1) (Signal.pure 0#1)
  let flag := Signal.mux isSLT flag_temp3 flag_temp2
  
  -- Concatenate flags into a 5-bit BitVec using bitwise operations
  -- flags[4:0] = {flag[0], overflow[0], negative[0], carry[0], zero[0]}
  let flags_bv := zero ||| (carry <<< 1) ||| (negative <<< 2) ||| (overflow <<< 3) ||| (flag <<< 4)
  
  bundle2 r flags_bv

#synthesizeVerilog alu
