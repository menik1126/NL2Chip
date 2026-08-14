import Sparkle.Compiler.Elab
import Tests.SymbolicParameterCircuits

#writeParameterizedCppSimDesign symbolicXor [W := 3] "/tmp/p3_cppsim_xor_w3.h"
#writeParameterizedCppSimDesign symbolicXor [W := 17] "/tmp/p3_cppsim_xor_w17.h"
#writeParameterizedCppSimDesign symbolicMemory [ADDR_W := 2, DATA_W := 8] "/tmp/p3_cppsim_memory_a2_d8.h"
#writeParameterizedCppSimDesign symbolicMemory [ADDR_W := 4, DATA_W := 17] "/tmp/p3_cppsim_memory_a4_d17.h"
#writeParameterizedCppSimDesign symbolicXorHierarchy [W := 17] "/tmp/p3_cppsim_hierarchy_w17.h"
#writeParameterizedCppSimDesign symbolicGenerateNot [W := 3] "/tmp/p3_cppsim_generate_w3.h"
#writeParameterizedCppSimDesign symbolicGenerateNot [W := 17] "/tmp/p3_cppsim_generate_w17.h"
#writeParameterizedCppSimDesign symbolicLoopBundle [W := 3] "/tmp/p3_cppsim_loop_bundle_w3.h"
#writeParameterizedCppSimDesign symbolicLoopBundle [W := 17] "/tmp/p3_cppsim_loop_bundle_w17.h"
#writeParameterizedCppSimDesign symbolicDepthCompare [DEPTH := 8] "/tmp/p3_cppsim_depth_compare_d8.h"
#writeParameterizedCppSimDesign symbolicDepthCompare [DEPTH := 12] "/tmp/p3_cppsim_depth_compare_d12.h"
#writeParameterizedCppSimDesign symbolicDepthCompare [DEPTH := 16] "/tmp/p3_cppsim_depth_compare_d16.h"
