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
