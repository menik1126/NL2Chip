import Sparkle.Compiler.Elab
import Tests.SignedHelperCircuits

#writeParameterizedCppSimDesign signedHelperSignExtend [W := 4] "/tmp/p3_cppsim_signed_sext_w4.h"
#writeParameterizedCppSimDesign signedHelperArithmeticShiftRight [W := 4] "/tmp/p3_cppsim_signed_asr_w4.h"
#writeParameterizedCppSimDesign signedHelperLT [W := 4] "/tmp/p3_cppsim_signed_lt_w4.h"
#writeParameterizedCppSimDesign signedHelperMulWide [W := 4] "/tmp/p3_cppsim_signed_mul_wide_w4.h"
#writeParameterizedCppSimDesign signedHelperMulTrunc [W := 4] "/tmp/p3_cppsim_signed_mul_trunc_w4.h"
#writeParameterizedCppSimDesign signedHelperMulShiftTrunc [W := 4] "/tmp/p3_cppsim_signed_mul_shift_w4.h"
#writeParameterizedCppSimDesign signedHelperSaturate [W := 8] "/tmp/p3_cppsim_signed_saturate_w8.h"
#writeCppSimDesign signedHelperSaturateC8 "/tmp/p3_cppsim_signed_saturate_c8.h"
#writeParameterizedCppSimDesign signedHelperAddTo [W := 4] "/tmp/p3_cppsim_signed_add_to_w4.h"
#writeParameterizedCppSimDesign signedHelperSubTo [W := 4] "/tmp/p3_cppsim_signed_sub_to_w4.h"
#writeParameterizedCppSimDesign signedHelperMulTo [W := 4] "/tmp/p3_cppsim_signed_mul_to_w4.h"
#writeParameterizedCppSimDesign signedHelperUnsignedDivOr [W := 4] "/tmp/p3_cppsim_unsigned_div_or_w4.h"
#writeParameterizedCppSimDesign signedHelperSignedDivOr [W := 4] "/tmp/p3_cppsim_signed_div_or_w4.h"
#writeParameterizedCppSimDesign signedHelperAbsTo [W := 4] "/tmp/p3_cppsim_signed_abs_to_w4.h"
#writeParameterizedCppSimDesign signedHelperMeanTowardZero [W := 4] "/tmp/p3_cppsim_signed_mean_tz_w4.h"
#writeParameterizedCppSimDesign signedHelperMeanFloor [W := 4] "/tmp/p3_cppsim_signed_mean_floor_w4.h"
#writeParameterizedCppSimDesign signedHelperDivPow2TowardZero [W := 4] "/tmp/p3_cppsim_signed_div_pow2_tz_w4.h"
#writeParameterizedCppSimDesign signedHelperDotPacked
  [LANES := 3, LHSW := 4, RHSW := 3, ACCW := 10]
  "/tmp/p3_cppsim_signed_dot_packed.h"
#writeParameterizedCppSimDesign signedHelperSumPacked
  [LANES := 3, W := 4, ACCW := 10]
  "/tmp/p3_cppsim_signed_sum_packed.h"
